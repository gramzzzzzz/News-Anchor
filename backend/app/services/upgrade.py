"""Raising the collection's reliability without narrowing it.

At the limit on discovered sources (discovery.max_total), new publishers can only come in if others go. This step adds well-rated
publishers the collection lacks and, once one of them is collecting and has its own trust rating, swaps out a weaker source of the
same country, but only where that costs no breadth:

* Who comes in. Domains rated well by the published expert ratings the trust score already uses (Lin et al. 2023, combined score 0.65
  or more; Wikipedia's perennial-sources list, "generally reliable") that are not in the registry. Their country comes from the
  country-code domain, else from Media Cloud's directory (which also gives the language). Countries with the fewest sources come first.
  They may enter a country at its limit, and exceed the total by discovery.upgrade_pool while they wait, because each one that
  starts collecting replaces a weaker source of its country (below); one that finds nothing to replace stays as an extra.
* When. A newcomer replaces a source once it collects and is rated reliable or better, and only if the collection is over its total
  or the newcomer's country is over its limit (discovery.country_cap). Under both limits it is simply added.
* Who goes. Only a source of the newcomer's own country (so the spread across countries does not change), with a lower trust band
  (low, then mixed; unrated only if discovery.upgrade_unrated is on), and only if removing it leaves every "cell" it covers with at least
  discovery.diversity_floor other sources. A cell is a (country, topic) or (country, sub-topic) pair, a state or city it reports on, its
  (country, language), and its (country, source type), all from its articles of the last 14 days. A source that is one of the few
  covering a place, a subject or a language is never swapped out, however low its rating.
* Reversible. A swapped-out source keeps its feeds and history (status "swapped", the newcomer named in its note); turning
  discovery.upgrade off brings every one back, and "Look for its feed again" brings one back.

Each run measures the collection's breadth (countries, cells, the evenness of sources across countries and of articles across topics)
and its reliability (share of sources and of articles from reliable or highly reliable outlets), before and after, on the Status page."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
from collections import Counter, defaultdict

import httpx

from app.dao.discovery.mediacloud import MediaCloudError

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
RANK = {"low": 0, "unrated": 1, "mixed": 2, "reliable": 3, "highly_reliable": 4}
WORKING = ("active", "degraded", "dormant")


def entropy(counts) -> float:
    """Evenness in bits: log2 of the number of equally used groups the spread is worth."""
    tot = sum(counts)
    return -sum(c / tot * math.log2(c / tot) for c in counts if c) if tot else 0.0


class ReliabilityUpgrade:
    def __init__(self, repo, settings, discovery, gazetteer=None):
        self.repo, self.settings, self.disc, self.gaz = repo, settings, discovery, gazetteer
        self.mc_gap = 1.0

    # ---- breadth and reliability ------------------------------------------------------------------
    def cells(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        """({cell: sources covering it}, {source: row}) for working discovered and curated sources (social left out)."""
        src = {r["key"]: r for r in self.repo.q(f"""SELECT key, country, language, type, band, curated, origin, name FROM sources
                                                    WHERE status IN {WORKING} AND blocked = false AND type <> 'social'""")}
        cover: dict[str, set[str]] = defaultdict(set)
        for k, r in src.items():
            cover[f"lang:{r['country']}:{r['language']}"].add(k)
            cover[f"type:{r['country']}:{r['type']}"].add(k)
        fine = {c for c, p in (self.gaz.places.items() if self.gaz else []) if p.level in ("state", "city")}
        topics = set(self.disc.tax.topics) if self.disc.tax else set()
        for r in self.repo.q("""SELECT a.source_key, t AS code, count(*) AS n FROM articles a CROSS JOIN LATERAL unnest(a.topics || a.places) AS t
                                WHERE a.seen_day >= current_date - 14 GROUP BY 1, 2 HAVING count(*) >= 2"""):
            s = src.get(r["source_key"])
            if not s:
                continue
            code = r["code"]
            if code in fine:
                cover[f"place:{code}"].add(r["source_key"])
            elif code in topics:
                cover[f"topic:{s['country']}:{code}"].add(r["source_key"])
        return cover, src

    def measure(self, cover: dict[str, set[str]] | None = None, src: dict[str, dict] | None = None) -> dict:
        if cover is None:
            cover, src = self.cells()
        by_country = Counter(r["country"] for r in src.values())
        bands = Counter(r["band"] for r in src.values())
        arts = self.repo.q("""SELECT s.band, count(*) AS n FROM articles a JOIN sources s ON s.key = a.source_key
                              WHERE a.seen_day >= current_date - 7 AND s.type <> 'social' GROUP BY 1""")
        art_tot = sum(r["n"] for r in arts) or 1
        topics = self.repo.q("""SELECT t AS code, count(*) AS n FROM articles a CROSS JOIN LATERAL unnest(a.topics) AS t
                                WHERE a.seen_day >= current_date - 7 AND position('.' in t) = 0 GROUP BY 1""")
        good = ("reliable", "highly_reliable")
        return {"sources": len(src), "countries": len([c for c in by_country if c != "XX"]),
                "cells": len(cover), "cells_thin": sum(1 for v in cover.values() if len(v) < 3),
                "country_evenness_bits": round(entropy(by_country.values()), 2), "topic_evenness_bits": round(entropy([r["n"] for r in topics]), 2),
                "reliable_share_sources": round(sum(bands[b] for b in good) / max(1, len(src)), 3),
                "reliable_share_articles": round(sum(r["n"] for r in arts if r["band"] in good) / art_tot, 3),
                "low_or_mixed_sources": bands["low"] + bands["mixed"], "unrated_sources": bands["unrated"]}

    # ---- bringing rated publishers in ---------------------------------------------------------------
    def rated_missing(self, min_score: float, limit: int) -> list[dict]:
        return self.repo.q("""WITH r AS (SELECT domain, max(value) AS value, string_agg(dataset || ' ' || round(value::numeric, 2) || CASE WHEN label <> '' THEN ' (' || label || ')' ELSE '' END, '; ') AS why
                                         FROM ref_scores WHERE (dataset = 'lin2023' AND value >= %s) OR (dataset = 'rsp' AND value >= 0.75) GROUP BY domain)
                              SELECT r.* FROM r WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.domain = r.domain OR s.domain = 'www.' || r.domain)
                              AND r.domain !~ '\\.(edu|mil)$' AND NOT EXISTS (SELECT 1 FROM kv WHERE key = 'upgrade_tried' AND value ? r.domain)
                              ORDER BY r.value DESC LIMIT %s""", (min_score, limit))

    async def seed(self, n: int) -> dict:
        from app.services.discovery import country_from_domain, source_type_for
        pool = int(self.settings.get("discovery.upgrade_pool"))
        have = await asyncio.to_thread(self.repo.one, "SELECT count(*) AS n FROM sources WHERE curated = false AND type <> 'social' AND status IN ('candidate', 'active', 'degraded', 'dormant')")
        room = int(self.settings.get("discovery.max_total")) + pool - have["n"]
        waiting = (await asyncio.to_thread(self.repo.one, "SELECT count(*) AS n FROM sources WHERE origin = 'ratings' AND status = 'candidate'"))["n"]
        room = min(room, pool - waiting)
        if room <= 0:
            return {"added": 0, "note": "the pool of rated newcomers waiting is full"}
        rows = await asyncio.to_thread(self.rated_missing, float(self.settings.get("discovery.upgrade_min_score")), max(n * 4, 50))
        add, known_site, counts = await self.disc._adder()
        valid = self.disc._valid_codes()
        target = self.disc._caps()[0]
        token = self.disc.token()
        mc = self.disc.mc_factory(token) if token else None
        tried = set(await asyncio.to_thread(self.repo.kv_get, "upgrade_tried") or [])
        plan = []
        for r in rows:
            if known_site(r["domain"]):
                tried.add(r["domain"])
                continue
            c = country_from_domain(r["domain"], valid)
            plan.append((c, r))
        # countries under the target first, then the best rated; domains whose country is not in the address are asked of Media Cloud
        plan.sort(key=lambda x: (not (x[0] and counts.get(x[0], 0) < target), -x[1]["value"]))
        added = asked = 0
        try:
            for c, r in plan:
                if added >= min(n, room):
                    break
                lang, mc_id = "", None
                if mc:
                    try:
                        hits = await mc.search_source(r["domain"])
                        asked += 1
                        await asyncio.sleep(self.mc_gap)
                    except (MediaCloudError, httpx.HTTPError, OSError, ValueError):     # the directory is a help, not a requirement
                        hits = []
                    h = next((x for x in hits if (x.get("name") or "").lower().removeprefix("www.") == r["domain"]), None)
                    if h:
                        c = c or self.disc.iso3to2.get((h.get("pub_country") or "").upper(), "")
                        lang, mc_id = (h.get("primary_language") or "").lower(), h.get("id")
                tried.add(r["domain"])
                if lang and not self.disc.lang_ok(lang):
                    continue
                country = c or "XX"
                rec = {"name": r["domain"], "domain": r["domain"], "country": country, "scope": "national" if country != "XX" else "international",
                       "type": source_type_for(r["domain"]), "language": lang or "en", "origin": "ratings", "mc_id": mc_id, "homepage": f"https://{r['domain']}/",
                       "media_type": None, "stories_per_week": None,
                       "note": f"Added to raise reliability: rated {r['why']}"[:300]}
                if await asyncio.to_thread(add, rec, True):
                    added += 1
        finally:
            if mc:
                await mc.close()
            await asyncio.to_thread(self.repo.kv_set, "upgrade_tried", sorted(tried)[-20000:])
        return {"added": added, "asked_media_cloud": asked, "candidates_left": max(0, len(rows) - len(plan))}

    # ---- swapping weaker sources out --------------------------------------------------------------
    def swap(self) -> dict:
        floor = int(self.settings.get("discovery.diversity_floor"))
        allow_unrated = bool(self.settings.get("discovery.upgrade_unrated"))
        over = self.repo.one("""SELECT count(*) AS n FROM sources WHERE curated = false AND type <> 'social'
                                AND status IN ('candidate', 'active', 'degraded', 'dormant')""")["n"] - int(self.settings.get("discovery.max_total"))
        per_country = self.repo.country_source_counts()
        cap = self.disc._caps()[1]
        paired: dict[str, str] = self.repo.kv_get("upgrade_paired") or {}
        cover, src = self.cells()
        before = self.measure(cover, src)
        newcomers = [r for r in src.values() if r["origin"] == "ratings" and r["key"] not in paired and r["band"] in ("reliable", "highly_reliable")]
        newcomers.sort(key=lambda r: -RANK[r["band"]])
        of_source: dict[str, list[str]] = defaultdict(list)
        for cell, ks in cover.items():
            for k in ks:
                of_source[k].append(cell)
        vol = {r["source_key"]: r["n"] for r in self.repo.q("SELECT source_key, count(*) AS n FROM articles WHERE seen_day >= current_date - 14 GROUP BY 1")}
        swaps, kept_for_breadth = [], 0
        for u in newcomers:
            # a newcomer replaces a source when the collection is over its total, or its own country is over the per-country limit
            if over <= 0 and per_country.get(u["country"], 0) <= (cap * 2 if u["country"] == "XX" else cap):
                continue
            limit_rank = RANK[u["band"]]
            cands = [r for r in src.values() if r["country"] == u["country"] and not r["curated"] and r["key"] != u["key"]
                     and RANK.get(r["band"], 1) < limit_rank and r["band"] in (("low", "mixed", "unrated") if allow_unrated else ("low", "mixed"))
                     and r["origin"] != "ratings"]
            cands.sort(key=lambda r: (RANK.get(r["band"], 1), vol.get(r["key"], 0)))
            victim = None
            for v in cands:
                if all(len(cover[c]) - 1 >= floor for c in of_source.get(v["key"], [])):
                    victim = v
                    break
                kept_for_breadth += 1
            if not victim:
                continue
            for c in of_source.get(victim["key"], []):
                cover[c].discard(victim["key"])
            src.pop(victim["key"])
            paired[u["key"]] = victim["key"]
            swaps.append((victim["key"], u["key"], u["name"], u["band"]))
            over -= 1
            per_country[u["country"]] = per_country.get(u["country"], 0) - 1
        if swaps:
            with self.repo.pool.connection() as c, c.cursor() as cur:
                for vk, uk, uname, band in swaps:
                    cur.execute("""UPDATE sources SET status = 'swapped', status_changed_at = now(),
                                   status_note = %s WHERE key = %s""",
                                (f"Swapped for {uname} ({band.replace('_', ' ')}), of the same country, to raise reliability at the source limit. "
                                 "Every place, subject and language it covered still has other sources. Look for its feed again to bring it back.", vk))
                    cur.execute("INSERT INTO source_history (source_key, change, reason) VALUES (%s, 'swapped', %s)", (vk, f"for {uk}"))
            self.repo.kv_set("upgrade_paired", paired)
        after = self.measure(cover, src) if swaps else before
        return {"swapped": len(swaps), "over_limit": max(0, over), "newcomers_waiting": max(0, len(newcomers) - len(swaps)),
                "protected_for_breadth": kept_for_breadth, "before": before, "after": after}

    def restore_all(self) -> int:
        n = self.repo.run("""UPDATE sources s SET status = CASE WHEN EXISTS (SELECT 1 FROM feeds f WHERE f.source_key = s.key AND f.state NOT IN ('pending', 'retired', 'blocked'))
                             THEN 'active' ELSE 'candidate' END, status_changed_at = now(), status_note = 'Back: swapping for reliability was turned off.'
                             WHERE status = 'swapped'""")
        self.repo.kv_set("upgrade_paired", {})
        return n

    async def run(self) -> dict:
        if not self.settings.get("discovery.upgrade"):
            n = await asyncio.to_thread(self.restore_all)
            return {"off": True, "restored": n}
        res = {"seed": await self.seed(int(self.settings.get("discovery.upgrade_batch")))}
        res["swap"] = await asyncio.to_thread(self.swap)
        await asyncio.to_thread(self.repo.kv_set, "upgrade_last", {"at": dt.datetime.now(UTC).isoformat(), **res})
        return res
