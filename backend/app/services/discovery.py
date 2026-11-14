"""Source discovery and lifecycle: grow the registry, find and validate each new source's feed, and retire what stops working.
Nothing here decides what is shown: a validated source starts as 'unrated' and the trust crawler rates it.

Where publishers come from (each runs on its own schedule, side by side):
* directory  Media Cloud's country collections and Wikidata outlets with feeds (weekly; resumable).
* stream     GDELT's 15-minute files: every publisher GDELT's crawlers saw, in English and 65 other languages, with where its stories happen.
* editions   Google News' 81 country editions, front page and sections, plus local pages for places the collection covers thinly.
* wikidata   each country's best-known outlets in Wikidata (most Wikipedia articles first), countries with the fewest sources first.
* web        news searches for the subjects and places the collection covers thinnest.
Balance: a country under its target is served first at every step (promotion, feed checks), a country at its cap takes no more, and
the rebalance job parks the excess of an over-full country (reversibly).

Stages of a source: candidate (found; no validated feed yet) -> active -> degraded -> dormant -> retired.
no_feed means the search found no usable feed; it is retried after 30 days (then 60, 90 ... up to 180)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.dao.discovery import feedfind, gdelt, websearch, wikidata_feeds
from app.dao.feeds import homepage
from app.dao.discovery.mediacloud import MediaCloud, MediaCloudError
from app.dao.trust.domains import registrable
from app.services.textutil import detect_lang

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
DATA = Path(__file__).resolve().parent.parent / "data"
_OFFICIAL = re.compile(r"(^|\.)(gov|nic|gob|gouv|govt|go|admin)(\.[a-z]{2})?$|\.(gov|nic|gob|gouv)\.", re.I)
_EN_WORDS = {"the", "and", "of", "to", "in", "for", "on", "with", "is", "at", "as", "by", "from", "after", "over", "says", "new", "will"}
AGGREGATORS = {"news.google.com", "msn.com", "yahoo.com", "dailyhunt.in", "newsbreak.com", "flipboard.com", "smartnews.com", "feedspot.com", "ground.news", "allsides.com",
               "newsnow.co.uk", "apple.news", "bing.com", "newsweb.com", "pressreader.com", "newsbytesapp.com", "inshorts.com", "upday.com", "gnews.org", "prnewswire.com",
               "businesswire.com", "globenewswire.com", "einpresswire.com", "newswire.com", "accesswire.com", "openpr.com", "pr.com", "prlog.org"}
KNOWN_SKIP = {"facebook.com", "twitter.com", "x.com", "youtube.com", "reddit.com", "medium.com", "substack.com", "blogspot.com", "wordpress.com", "tumblr.com",
              "wikipedia.org", "yahoo.com", "msn.com", "google.com", "linkedin.com", "pinterest.com", "instagram.com", "t.me", "archive.org", "flipboard.com"}


def host_of(url_or_domain: str) -> str:
    s = url_or_domain.strip().lower()
    if "//" not in s:
        s = "//" + s
    return (urlsplit(s).hostname or "").removeprefix("www.")


def site_of(url: str) -> str:
    """The publisher's site of an article address: its host without www., m., amp. or mobile."""
    h = host_of(url)
    for pre in ("m.", "amp.", "mobile."):
        if h.startswith(pre) and h.count(".") >= 2:
            return h[len(pre):]
    return h


def make_key(domain: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:56] or "source"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    taken.add(key)
    return key


def looks_english(titles: list[str]) -> bool:
    words = [w for t in titles for w in re.findall(r"[a-zA-Z']+", t.lower())]
    letters = sum(len(re.findall(r"[^\W\d_]", t)) for t in titles)
    ascii_letters = sum(len(re.findall(r"[a-zA-Z]", t)) for t in titles)
    if not words or not letters or ascii_letters / letters < 0.9:
        return False
    return sum(1 for w in words if w in _EN_WORDS) / len(words) >= 0.08


GENERIC_TLDS = {"io", "tv", "co", "me", "ai", "fm", "cc", "ly", "gg", "to", "ws", "nu", "vc", "gl", "ms", "cx", "tk", "xyz", "app", "dev"}


def country_from_domain(domain: str, valid: set[str]) -> str:
    """Country of a publisher from its country-code domain (bbc.co.uk -> GB). Empty when the domain says nothing (.com, .org, .tv ...).
    The search edition a site turned up in says where readers are, not where the publisher is, so it is never used."""
    tld = domain.rsplit(".", 1)[-1].lower()
    if len(tld) != 2 or tld in GENERIC_TLDS:
        return ""
    code = "GB" if tld == "uk" else tld.upper()
    return code if code in valid else ""


def source_type_for(domain: str) -> str:
    return "official" if _OFFICIAL.search(domain) else "news"


class DiscoveryService:
    def __init__(self, repo, settings, fetcher, bus, token_fallback: str = "", http_factory=None, mc_factory=None, taxonomy=None, gazetteer=None):
        self.repo, self.settings, self.fetcher, self.bus = repo, settings, fetcher, bus
        self.tax, self.gaz = taxonomy, gazetteer
        self.web_sleep = 6.0                           # seconds between searches (GDELT allows one request every 5)
        self.token_fallback = token_fallback
        self.http_factory = http_factory or (lambda: httpx.AsyncClient(headers={"User-Agent": fetcher.ua}, follow_redirects=True))
        self.mc_factory = mc_factory or (lambda token: MediaCloud(token, fetcher.ua))
        self._dir_lock = asyncio.Lock()
        self._batch_lock = asyncio.Lock()
        self.dir_running = False                       # reading the directories (long: minutes to hours, paced for the services)
        self.batch_running = False                     # looking for the feeds of waiting candidates (runs alongside)
        self.web_running = False                       # searching the web for publishers (runs alongside)
        self._web_lock = asyncio.Lock()
        self.stream_running = self.editions_running = self.wd_running = False
        self.page_sleep = 3.0                          # seconds between Google News edition pages
        self.decode_sleep = 1.0                        # seconds between opening Google News links
        self.upgrader = None                           # ReliabilityUpgrade: rated newcomers in, weaker sources out, breadth kept
        self.on_cards = None                           # async callable(cards) -> dict: stores story cards of known publishers (IngestService.ingest_cards)
        self.wd_sleep = 2.0                            # seconds between Wikidata questions
        self.fips = {}
        for line in (DATA / "geonames_countries.tsv").read_text(encoding="utf-8").splitlines():
            cols = line.split("\t")
            if len(cols) >= 2 and cols[1]:
                self.fips[cols[1]] = cols[0]
        a2 = {c["alpha-3"]: c["alpha-2"] for c in json.loads((DATA / "countries.json").read_text(encoding="utf-8"))}
        self.iso3to2 = a2

    # ---- configuration ---------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.dir_running or self.batch_running or self.web_running or self.stream_running or self.editions_running or self.wd_running

    def token(self) -> str:
        return (self.settings.get("mediacloud.token") or self.token_fallback or "").strip()

    def languages(self) -> list[str]:
        return list(self.settings.get("sources.languages"))

    def lang_ok(self, lang: str) -> bool:
        """Whether sources in this language are collected (all languages are, unless the owner has limited them)."""
        if self.settings.get("sources.all_languages"):
            return True
        langs = set(self.languages())
        return (lang or "").lower() in langs or (not lang and ("en" in langs or "und" in langs))

    def _caps(self) -> tuple[int, int]:
        return int(self.settings.get("discovery.country_target")), int(self.settings.get("discovery.country_cap"))

    def _full(self, country: str, counts: dict[str, int]) -> bool:
        """A country at its cap takes no more sources (unknown-country publishers get twice the room: they are many small sites)."""
        cap = self._caps()[1]
        return counts.get(country, 0) >= (cap * 2 if country in ("", "XX") else cap)

    def status(self) -> dict:
        return {"running": self.running, "counts": self.repo.source_status_counts(), "feeds": self.repo.feed_counts(),
                "pending_feed_search": self.repo.candidates_pending(), "last_directory": self.repo.kv_get("discovery_last_directory"),
                "last_batch": self.repo.kv_get("discovery_last_batch"), "last_web": self.repo.kv_get("discovery_last_web"), "web": self.repo.web_stats(),
                "web_running": self.web_running, "directory_busy": self.dir_running,
                "last_stream": self.repo.kv_get("discovery_last_stream"), "last_editions": self.repo.kv_get("discovery_last_editions"),
                "last_wikidata": self.repo.kv_get("discovery_last_wikidata"), "last_rebalance": self.repo.kv_get("discovery_last_rebalance"),
                "directory_resume": len(self.repo.kv_get("mc_done") or []), "coverage": self.coverage(),
                "busy": {"stream": self.stream_running, "editions": self.editions_running, "wikidata": self.wd_running}, "batch_busy": self.batch_running, "mediacloud_token": bool(self.token()), "last_error": self.repo.kv_get("discovery_last_error")}

    def due_all(self) -> list[str]:
        """Everything the scheduler should start now (they run side by side): 'directory', 'batch', 'web', 'lifecycle'."""
        if not self.settings.get("discovery.enabled"):
            return []
        out: list[str] = []
        now = dt.datetime.now(UTC)
        last = self.repo.kv_get("discovery_last_directory")
        tried = self.repo.kv_get("discovery_last_attempt")
        if self.dir_running or (tried and now - dt.datetime.fromisoformat(tried) < dt.timedelta(hours=1)):
            pass                                                   # a failed directory run is retried after an hour, never in a loop
        elif (not last or self.repo.kv_get("mc_done") or now - dt.datetime.fromisoformat(last["at"]) > dt.timedelta(days=int(self.settings.get("discovery.refresh_days")))):
            out.append("directory")                                # an interrupted read (restart, error) resumes where it stopped
        b = self.repo.kv_get("discovery_last_batch")
        if not self.batch_running and self.repo.candidates_pending() and (not b or now - dt.datetime.fromisoformat(b["at"]) > dt.timedelta(minutes=1)):
            out.append("batch")
        w = self.repo.kv_get("discovery_last_web")
        if (self.settings.get("discovery.use_web") and not self.web_running and self._room_left() > 0
                and (not w or now - dt.datetime.fromisoformat(w["at"]) > dt.timedelta(hours=float(self.settings.get("discovery.web_hours"))))):
            out.append("web")
        def every(key: str, minutes: float) -> bool:
            last = self.repo.kv_get(key)
            at = last.get("at") if isinstance(last, dict) else last
            return not at or now - dt.datetime.fromisoformat(at) > dt.timedelta(minutes=minutes)

        if self.settings.get("discovery.use_gdelt") and not self.stream_running and every("discovery_last_stream", float(self.settings.get("discovery.gdelt_minutes"))):
            out.append("stream")
        if self.settings.get("discovery.use_editions") and not self.editions_running and every("discovery_last_editions", float(self.settings.get("discovery.edition_minutes"))):
            out.append("editions")
        if self.settings.get("discovery.use_wikidata") and not self.wd_running and every("discovery_last_wikidata", 60) and self._room_left() > 0:
            out.append("wikidata")
        if every("discovery_last_rebalance", 360):
            out.append("rebalance")                                # with parking off, this only brings back anything parked earlier
        lc = self.repo.kv_get("lifecycle_last")
        if not lc or now - dt.datetime.fromisoformat(lc) > dt.timedelta(hours=24):
            out.append("lifecycle")
        return out

    def due(self) -> str | None:
        d = self.due_all()
        return d[0] if d else None

    def _room_left(self) -> int:
        """Room under the limit on discovered sources. The limit counts sources that collect or wait to: those without a feed, retired,
        parked or swapped out cost nothing and do not count (until 2 Nov 2026 they did, so 3,000 such sources held places)."""
        have = self.repo.one("""SELECT count(*) AS n FROM sources WHERE curated = false AND type <> 'social'
                                AND status IN ('candidate', 'active', 'degraded', 'dormant')""")["n"]
        return int(self.settings.get("discovery.max_total")) - have

    # ---- stage 1: the directories ----------------------------------------------------------------
    async def run_directory(self, job_id: int | None = None) -> dict:
        if self._dir_lock.locked():
            return {"skipped": "already running"}
        async with self._dir_lock:
            self.dir_running = True
            await asyncio.to_thread(self.repo.kv_set, "discovery_last_attempt", dt.datetime.now(UTC).isoformat())
            try:
                res = await self._directory(job_id)
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_directory", {"at": dt.datetime.now(UTC).isoformat(), **res})
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_error", None)
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="done", result=res)
                return res
            except Exception as e:
                log.exception("Discovery failed")
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_error", f"{type(e).__name__}: {str(e)[:200]}")
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="failed", result={"error": str(e)[:200]})
                return {"error": str(e)}
            finally:
                self.dir_running = False

    async def _step(self, job_id: int | None, step: str, done: int = 0, total: int = 0) -> None:
        p = {"step": step, "done": done, "total": total}
        self.bus.publish({"type": "discovery-progress", **p})
        if job_id:
            await asyncio.to_thread(self.repo.job_update, job_id, state="running", progress=p)

    async def _adder(self):
        """A function that adds one candidate unless its site is known, skipped, or its country is full. Shared by every finder in a run."""
        known = await asyncio.to_thread(self.repo.known_domains)
        taken = await asyncio.to_thread(self.repo.source_keys)
        counts = await asyncio.to_thread(self.repo.country_source_counts)
        # a site counts as known when its own address is, or when the outlet's main site is (feeds.bbc.co.uk under bbc.co.uk).
        # An edition on a subdomain (francais.rt.com) does not make the main site (rt.com) known: they are different outlets' worth of news.
        exact = {d.lower().removeprefix("www.") for d in known}
        known_reg = {d for d in exact if registrable(d) == d}

        def is_known(dom: str) -> bool:
            return dom in exact or registrable(dom) in known_reg

        def add(rec: dict, ignore_cap: bool = False) -> str | None:
            """``ignore_cap``: a rated newcomer may enter a full country, because it will replace a weaker source of that country."""
            dom = rec["domain"]
            reg = registrable(dom)
            if (is_known(dom) or reg in KNOWN_SKIP or dom in KNOWN_SKIP or dom in AGGREGATORS or reg in AGGREGATORS or "." not in dom
                    or (not ignore_cap and self._full(rec.get("country") or "XX", counts))):
                return None
            rec["key"] = make_key(dom, taken)
            if self.repo.candidate_add(rec):
                exact.add(dom)
                if reg == dom:
                    known_reg.add(reg)
                counts[rec.get("country") or "XX"] = counts.get(rec.get("country") or "XX", 0) + 1
                return rec["key"]
            return None

        def known_site(dom: str) -> bool:
            reg = registrable(dom)
            return is_known(dom) or reg in KNOWN_SKIP or dom in KNOWN_SKIP or dom in AGGREGATORS or reg in AGGREGATORS or "." not in dom

        return add, known_site, counts

    async def _directory(self, job_id: int | None) -> dict:
        added = {"mediacloud": 0, "wikidata": 0}
        notes: list[str] = []
        cap = int(self.settings.get("discovery.per_country_cap"))
        min_spw = float(self.settings.get("discovery.min_stories_per_week"))
        add, _, _ = await self._adder()
        done_ids: set[int] = set(self.repo.kv_get("mc_done") or [])     # collections already read in this pass (a restart resumes after them)

        token = self.token()
        if token and self.settings.get("discovery.use_mediacloud"):
            mc = self.mc_factory(token)
            try:
                await self._step(job_id, "Media Cloud: country collections")
                cols = await mc.national_collections(include_local=bool(self.settings.get("discovery.include_local")))
                home = self._country_name(self.settings.get("home.country"))
                national = sorted([c for c in cols if c["name"].endswith(" - National")], key=lambda c: (not c["name"].startswith(home), -c["source_count"]))
                local = sorted([c for c in cols if not c["name"].endswith(" - National")], key=lambda c: (home not in c["name"], -c["source_count"]))
                this_month = dt.date.today().replace(day=1)
                max_total = int(self.settings.get("discovery.max_total"))
                local_cap = int(self.settings.get("discovery.local_cap"))
                have = int(self.settings.get("discovery.max_total")) - await asyncio.to_thread(self._room_left)      # the same count as everywhere else
                ordered = [(c, cap) for c in national] + [(c, local_cap) for c in local]
                failed_in_a_row = 0
                for i, (col, ccap) in enumerate(ordered, 1):
                    if have + added["mediacloud"] + added["wikidata"] >= max_total:
                        notes.append(f"Stopped at the ceiling of {max_total} discovered sources (Settings → Sources).")
                        break
                    if col["id"] in done_ids:
                        continue
                    await self._step(job_id, f"Media Cloud: {col['name']}", i, len(ordered))
                    try:
                        rows = await mc.collection_sources(col["id"])
                        failed_in_a_row = 0
                    except MediaCloudError as e:
                        notes.append(f"{col['name']}: {e}")
                        failed_in_a_row += 1
                        if failed_in_a_row >= 5:             # the same failure again and again is the connection or the token, not these collections
                            notes.append(f"Stopped after {failed_in_a_row} collections in a row failed ({e}); the next run continues where this one stopped.")
                            break
                        continue
                    # Media Cloud's story counts measure Media Cloud's own crawler, not the outlet: it records 0 a week for rt.com,
                    # newsweek.com, zeit.de and irna.ir, whose crawls it lost. So they never exclude a source; our own feed check
                    # (recent items) decides whether a site publishes. Sources listed in many collections (better known) go first.
                    picks = []
                    for r in rows:
                        lang = (r.get("primary_language") or "").lower()
                        spw = r.get("stories_per_week") or 0
                        domain = host_of(r.get("homepage") or r.get("name") or "")
                        if not domain or not self.lang_ok(lang) or (0 < spw < min_spw):
                            continue
                        if r.get("last_story") and not self._recent(r["last_story"], this_month):
                            continue                                    # nothing in Media Cloud for three years: very likely gone
                        picks.append(((r.get("collection_count") or 0, spw), r, domain, lang))
                    picks.sort(key=lambda x: (-x[0][0], -x[0][1]))
                    for (_cc, spw), r, domain, lang in picks[:ccap]:
                        country = self.iso3to2.get(r.get("pub_country") or "", "") or self._country_from_collection(col["name"])
                        rec = {"name": (r.get("label") or r.get("name") or domain)[:120], "domain": domain, "country": country or "XX", "type": source_type_for(domain),
                               "language": lang or "en", "origin": "mediacloud", "mc_id": r["id"], "homepage": r.get("homepage"), "media_type": r.get("media_type"),
                               "stories_per_week": spw, "note": f"Media Cloud: {col['name']}"}
                        if await asyncio.to_thread(add, rec):
                            added["mediacloud"] += 1
                    done_ids.add(col["id"])
                    await asyncio.to_thread(self.repo.kv_set, "mc_done", sorted(done_ids))
                done_ids.clear()                                    # the whole directory was read: the next pass starts afresh
                await asyncio.to_thread(self.repo.kv_set, "mc_done", None)
            except MediaCloudError as e:
                notes.append(f"Media Cloud: {e}")
            finally:
                await mc.close()
        elif not token:
            notes.append("Media Cloud skipped: no API token set (Settings, Sources).")

        if self.settings.get("discovery.use_wikidata"):
            await self._step(job_id, "Wikidata: outlets with feeds")
            try:
                async with self.http_factory() as http:
                    outs = await wikidata_feeds.outlets(http)
                for o in outs:
                    lang = (o["language"] or "").lower()
                    if not self.lang_ok(lang):
                        continue
                    domain = host_of(o["site"])
                    if not domain:
                        continue
                    rec = {"name": o["name"][:120], "domain": domain, "country": (o["country"] or "XX").upper(), "type": source_type_for(domain),
                           "language": lang or "en", "origin": "wikidata", "mc_id": None, "homepage": o["site"], "media_type": None,
                           "stories_per_week": None, "note": f"Wikidata {o['qid']}"}
                    key = await asyncio.to_thread(add, rec)
                    if key:
                        added["wikidata"] += 1
                        for f in o["feeds"][:3]:
                            await asyncio.to_thread(self.repo.feed_pending_add, key, f, "rss", "wikidata")
            except (httpx.HTTPError, RuntimeError, ValueError) as e:
                notes.append(f"Wikidata: {e}")
        counts = await asyncio.to_thread(self.repo.source_status_counts)
        return {"added": added, "notes": notes, "sources": counts}

    @staticmethod
    def _recent(last_story: str, this_month: dt.date) -> bool:
        try:
            m, y = (int(x) for x in last_story.split("/"))
            return (this_month.year - y) * 12 + (this_month.month - m) <= 36
        except ValueError:
            return True

    def _country_name(self, code: str) -> str:
        for c in json.loads((DATA / "countries.json").read_text(encoding="utf-8")):
            if c["alpha-2"] == code:
                return c["name"].split(" (")[0]
        return code

    def _country_from_collection(self, name: str) -> str:
        base = name.rsplit(" - ", 1)[0].split(",")[0].strip()
        for c in json.loads((DATA / "countries.json").read_text(encoding="utf-8")):
            if c["name"].split(" (")[0] == base:
                return c["alpha-2"]
        return ""

    # ---- stage 2: feeds for candidates -----------------------------------------------------------
    async def run_batch(self, n: int | None = None, job_id: int | None = None) -> dict:
        if self._batch_lock.locked():
            return {"skipped": "already running"}
        async with self._batch_lock:
            self.batch_running = True
            try:
                res = await self._batch(n or int(self.settings.get("discovery.feed_search_batch")))
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_batch", {"at": dt.datetime.now(UTC).isoformat(), **res})
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="done", result=res)
                return res
            except Exception as e:
                log.exception("Feed search failed")
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_batch", {"at": dt.datetime.now(UTC).isoformat(), "error": str(e)[:200]})
                return {"error": str(e)}
            finally:
                self.batch_running = False

    async def _batch(self, n: int) -> dict:
        cands = await asyncio.to_thread(self.repo.candidates_for_feed_search, n, self._caps()[0])
        self.fetcher.configure(self.settings.get("refresh.feed_timeout_s"), self.settings.get("refresh.host_delay_s"))
        await self.fetcher.start()
        sem = asyncio.Semaphore(12)
        out = {"searched": len(cands), "found": 0, "none": 0}
        token = self.token()
        mc = self.mc_factory(token) if token else None

        async def one(c: dict) -> None:
            async with sem:
                try:
                    found = await asyncio.wait_for(self.find_feeds(c, mc), timeout=90)      # a slow site must not hold up the batch
                except asyncio.TimeoutError:
                    log.info("Feed search for %s timed out", c["domain"])
                    found = None
                except Exception as e:                              # one bad site must never stop the batch
                    log.warning("Feed search for %s failed: %s", c["domain"], e)
                    found = None
                if found:
                    titles = found.pop("titles", [])
                    lang = detect_lang(" . ".join(titles[:25]), "" if c["language"] in ("en", "und", "xx") else c["language"])
                    interval = int(self.settings.get("refresh.news_minutes")) * 60
                    for f in found["feeds"]:
                        await asyncio.to_thread(self.repo.feed_discovered, c["key"], f["url"], f["kind"], c["origin"], interval)
                    await asyncio.to_thread(self.repo.source_feed_search_done, c["key"], True, f"{len(found['feeds'])} feed(s) found: {found['feeds'][0]['url']}"[:300])
                    if titles and lang and lang != c["language"]:          # the feed itself says what language the site publishes in
                        await asyncio.to_thread(self.repo.run, "UPDATE sources SET language = %s WHERE key = %s", (lang, c["key"]))
                    out["found"] += 1
                else:
                    await asyncio.to_thread(self.repo.source_feed_search_done, c["key"], False, "No usable feed, news sitemap or home page with article links found (site down, blocking programs, or not a news site).")
                    out["none"] += 1

        try:
            await asyncio.gather(*(one(c) for c in cands))
        finally:
            if mc:
                await mc.close()
        if out["found"]:
            await asyncio.to_thread(self.repo.kv_set, "trust_dirty", True)
            self.bus.publish({"type": "discovery", **out})
        return out

    async def find_feeds(self, c: dict, mc: MediaCloud | None) -> dict | None:
        """Validated feeds for one candidate source, or None. Order: addresses already listed for it, the home page's own links,
        common paths, Media Cloud's list; with none, the site's Google News sitemap; with neither, the home page itself (its article
        links). Up to two feeds are kept."""
        good: list[dict] = []

        async def try_url(u: str) -> bool:
            v = await feedfind.validate(self.fetcher, u)
            if v and v["url"] not in {g["url"] for g in good}:
                good.append(v)
                return True
            return False

        for p in await asyncio.to_thread(self.repo.pending_feeds, c["key"]):
            await try_url(p["url"])
            await asyncio.to_thread(self.repo.feed_delete, p["id"])           # a validated one is re-added as an active feed by the caller
            if len(good) >= 2:
                break
        home = c["homepage"] or f"https://{c['domain']}/"
        page, final = "", home
        if not good:
            r = await self.fetcher.get(home, accept="text/html,*/*;q=0.8")
            page = r.body.decode("utf-8", "replace") if r.body and not r.error else ""
            final = r.final_url or home
            tried = 0
            for u in feedfind.advertised(page, r.final_url or home)[:6]:
                if await try_url(u) and len(good) >= 2:
                    break
            if not good and r.body is not None and not r.error:         # the site answers, so its common paths are worth a few requests
                for u in feedfind.common_candidates(r.final_url or home):
                    tried += 1
                    if await try_url(u) or tried >= 6:
                        break
        if not good and mc and c.get("mc_id"):
            try:
                for f in (await mc.source_feeds(c["mc_id"]))[:12]:
                    if f.get("admin_rss_enabled") and await try_url(f["url"]) and len(good) >= 2:
                        break
            except MediaCloudError:
                pass
        if not good and self.settings.get("sources.sitemap_fallback"):
            # no feed: the site's Google News sitemap (what publishers make for Google News), at most 6 addresses tried
            listed = await self.fetcher.sitemaps(final) if page else []
            for k, u in enumerate(feedfind.sitemap_candidates(final, listed)):
                if k >= 6:
                    break
                v = await feedfind.validate_sitemap(self.fetcher, u)
                if v:
                    good.append(v)
                    break
        if not good and page and self.settings.get("sources.homepage_fallback"):
            # neither: the home page's own article links (new links on each visit become articles)
            links = homepage.article_links(page, final)
            if len(links) >= int(self.settings.get("sources.homepage_min_links")):
                good.append({"url": final, "kind": "homepage", "items": len(links), "newest": None, "titles": [x["title"] for x in links[:12]]})
        if not good:
            return None
        return {"feeds": good[:2], "titles": good[0]["titles"]}

    # ---- stage 3: health ------------------------------------------------------------------------
    async def run_lifecycle(self) -> dict:
        res = await asyncio.to_thread(self.repo.lifecycle_update, 90)
        await asyncio.to_thread(self.repo.kv_set, "lifecycle_last", dt.datetime.now(UTC).isoformat())
        return res

    # ---- stage 1b: searching the web ---------------------------------------------------------------
    async def run_web(self, n: int | None = None, job_id: int | None = None) -> dict:
        """Search news (Google News search by default, or GDELT's; one question every 6 seconds) for the subjects and places the collection
        covers thinnest; every publisher that turns up repeatedly and is not yet known becomes a candidate (its feed is then found and
        validated like any other). Results from publishers already collected are stored as their articles."""
        if self._web_lock.locked():
            return {"skipped": "already running"}
        async with self._web_lock:
            self.web_running = True
            try:
                res = await self._web(n or int(self.settings.get("discovery.web_queries")), job_id)
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_web", {"at": dt.datetime.now(UTC).isoformat(), **res})
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="done", result=res)
                return res
            except Exception as e:
                log.exception("Web discovery failed")
                await asyncio.to_thread(self.repo.kv_set, "discovery_last_web", {"at": dt.datetime.now(UTC).isoformat(), "error": f"{type(e).__name__}: {str(e)[:150]}"})
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, state="failed", result={"error": str(e)[:200]})
                return {"error": str(e)}
            finally:
                self.web_running = False

    SEARCH_EDITIONS = ("US", "GB", "IN", "AU", "CA", "NZ", "IE", "SG", "ZA", "NG", "KE", "PH", "PK", "MY", "GH", "JM", "LK", "BD")

    def plan_queries(self, n: int, done: dict[str, str]) -> list[dict]:
        """Queries for what the collection covers thinnest, interleaving subjects and places, skipping any asked in the last 14 days."""
        if not self.tax or not self.gaz:
            return []
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=14)).isoformat()
        recent = {q for q, t in done.items() if t > cutoff}
        prefs = self.repo.topic_prefs()
        tcount, pcount = self.repo.topic_story_counts(7), self.repo.place_story_counts(7)
        topics = [t for c, t in self.tax.topics.items() if t.depth == 1 or prefs.get(c)]
        topics.sort(key=lambda t: (tcount.get(t.code, 0), t.depth))
        places = [p for p in self.gaz.places.values() if p.level in ("country", "state", "city")]
        places.sort(key=lambda p: (pcount.get(p.code, 0), {"country": 0, "state": 1, "city": 2}[p.level]))
        home = self.settings.get("home.country")
        cursor = int(self.repo.kv_get("web_cursor", 0) or 0)
        out: list[dict] = []
        ti = pi = 0
        while len(out) < n and (ti < len(topics) or pi < len(places)):
            take_topic = (len(out) % 2 == 0 and ti < len(topics)) or pi >= len(places)
            if take_topic:
                t = topics[ti]; ti += 1
                words = list(dict.fromkeys(a for a in t.aliases if " " not in a or len(a) < 24))[:3] or [t.name]
                q = "(" + " OR ".join(f'"{w}"' if " " in w else w for w in words) + ")" if len(words) > 1 else words[0]
                gl = home if len(out) % 3 == 0 else self.SEARCH_EDITIONS[(cursor + len(out)) % len(self.SEARCH_EDITIONS)]
                label = t.name
            else:
                p = self.gaz.places[places[pi].code]; pi += 1
                q = f'"{p.name}"'
                gl = p.code.split("-")[0] if p.level != "country" else p.code
                label = p.name
            key = f"{q}|{gl}"
            if key in recent:
                continue
            out.append({"q": q, "gl": gl if len(gl) == 2 else "US", "label": label, "key": key})
        return out

    def _valid_codes(self) -> set[str]:
        return set(self.iso3to2.values())

    def _country_code(self, name: str) -> str:
        if not name:
            return ""
        if not hasattr(self, "_names"):
            self._names = {c["name"].split(" (")[0].lower(): c["alpha-2"] for c in json.loads((DATA / "countries.json").read_text(encoding="utf-8"))}
        return self._names.get(name.lower(), "")

    async def _web(self, n: int, job_id: int | None) -> dict:
        done: dict[str, str] = self.repo.kv_get("web_done", {}) or {}
        plan = await asyncio.to_thread(self.plan_queries, n, done)
        out = {"queries": 0, "domains_seen": 0, "promoted": 0, "notes": []}
        seen: set[str] = set()
        items: list[dict] = []                         # Google News results: their stories from publishers already collected are stored
        async with self.http_factory() as http:
            for i, qq in enumerate(plan, 1):
                await self._step(job_id, f"Searching the web: {qq['label']}", i, len(plan))
                found: dict[str, dict] = {}
                engine = self.settings.get("discovery.web_engine")
                try:
                    if engine == "gdelt":
                        for r in await websearch.gdelt(http, qq["q"]):
                            d = host_of(r["domain"])
                            if d:
                                f = found.setdefault(d, {"domain": d, "name": d, "hits": 0, "country": self._country_code(r["country"]), "language": "en",
                                                         "homepage": f"https://{d}/", "via": "gdelt_search", "example": qq["label"]})
                                f["hits"] += 1
                    else:
                        for r in await websearch.google_news(http, qq["q"], qq["gl"]):
                            d = site_of(r["site_url"])
                            if d:
                                f = found.setdefault(d, {"domain": d, "name": r["name"], "hits": 0, "country": "", "language": "", "homepage": r["site_url"],
                                                         "via": "google_news", "example": qq["label"]})
                                f["hits"] += 1
                                items.append({**r, "priority": 1})
                except websearch.WebRateLimited as e:
                    out["notes"].append(f"{'GDELT' if engine == 'gdelt' else 'Google News'}: {e}. Stopped early; the next run continues.")
                    break
                except httpx.HTTPError as e:
                    out["notes"].append(f"{'GDELT' if engine == 'gdelt' else 'Google News'}: {type(e).__name__}")
                for f in found.values():                       # each search counts once per publisher, however many results name it
                    await asyncio.to_thread(self.repo.web_hit_add, f)
                    seen.add(f["domain"])
                done[qq["key"]] = dt.datetime.now(UTC).isoformat()
                out["queries"] += 1
                await asyncio.sleep(self.web_sleep)
            if items and self.settings.get("discovery.edition_articles"):
                out.update(await self._store_gnews(items, http))
        # promote publishers seen in several searches
        out["domains_seen"] = len(seen)
        await asyncio.to_thread(self.repo.kv_set, "web_done", dict(sorted(done.items(), key=lambda kv: kv[1])[-600:]))
        await asyncio.to_thread(self.repo.kv_set, "web_cursor", int(self.repo.kv_get("web_cursor", 0) or 0) + len(plan))
        out["promoted"] = await self._promote_web_hits()
        return out

    # ---- where a publisher is, and promotion -------------------------------------------------------
    def resolve_country(self, h: dict) -> tuple[str, str]:
        """(country, why) for a publisher seen on the web. In order: its country-code domain; the one country whose national or local
        Google News pages carry it; the country where most of the events it reports take place (GDELT, when clear); the country a search
        service named; else unknown (XX, treated as international)."""
        valid = self._valid_codes()
        c = country_from_domain(h["domain"], valid)
        if c:
            return c, "from its country-code domain"
        ne = [x for x in (h.get("nation_editions") or []) if x in valid]
        if len(ne) == 1:
            return ne[0], f"Google News carries it on {ne[0]} national or local pages"
        focus = h.get("focus") or {}
        if isinstance(focus, str):
            focus = json.loads(focus)
        tot = sum(int(v) for v in focus.values())
        if tot >= 3:
            top = max(focus, key=lambda k: int(focus[k]))
            share = int(focus[top]) / tot
            if share >= 0.6 and top in valid:
                return top, f"{round(100 * share)}% of the events it reports happen there (GDELT)"
        if h.get("country") in valid:
            return h["country"], "named by the search service"
        return "XX", "not known, so treated as international"

    async def _promote_web_hits(self) -> int:
        """Publishers seen often enough become candidates: those from the countries with the fewest sources first. A country at its cap
        leaves its publishers waiting (a later run takes them if the cap is raised)."""
        add, known_site, counts = await self._adder()
        room = await asyncio.to_thread(self._room_left)
        ready = await asyncio.to_thread(self.repo.web_hits_ready, int(self.settings.get("discovery.web_min_hits")), int(self.settings.get("discovery.web_min_queries")),
                                        3000, int(self.settings.get("discovery.gdelt_min_files")))
        target = self._caps()[0]
        rows = []
        for h in ready:
            if known_site(h["domain"]):
                await asyncio.to_thread(self.repo.web_hit_promoted, h["domain"])
                continue
            country, why = self.resolve_country(h)
            rows.append(((counts.get(country, 0) >= target or country == "XX", counts.get(country, 0), -h["hits"]), h, country, why))
        rows.sort(key=lambda r: r[0])
        added = 0
        for _, h, country, why in rows:
            if room <= 0:
                break
            if not self.lang_ok(h["language"]) or self._full(country, counts):
                continue
            seen = []
            if h.get("queries"):
                seen.append(f"on {h['queries']} Google News pages or news searches")
            if h.get("files"):
                seen.append(f"in {h['files']} quarter hours of GDELT's world news stream")
            rec = {"name": (h["name"] or h["domain"])[:120], "domain": h["domain"], "country": country, "scope": "national" if country != "XX" else "international",
                   "type": source_type_for(h["domain"]), "language": h["language"] or "en", "origin": "web", "mc_id": None,
                   "homepage": h["homepage"] or f"https://{h['domain']}/", "media_type": None, "stories_per_week": None,
                   "note": (f"Found on the web: seen {' and '.join(seen) or 'in news searches'}; country: {why}"
                            + (f"; for example {h['example']}" if h.get("example") else ""))[:300]}
            if await asyncio.to_thread(add, rec):
                added += 1
                room -= 1
                await asyncio.to_thread(self.repo.web_hit_promoted, h["domain"])
        return added

    # ---- shared runner -----------------------------------------------------------------------------
    async def _run_job(self, flag: str, kv: str, fn, job_id: int | None = None) -> dict:
        if getattr(self, flag):
            return {"skipped": "already running"}
        setattr(self, flag, True)
        try:
            res = await fn(job_id)
            await asyncio.to_thread(self.repo.kv_set, kv, {"at": dt.datetime.now(UTC).isoformat(), **res})
            if job_id:
                await asyncio.to_thread(self.repo.job_update, job_id, state="done", result=res)
            return res
        except Exception as e:
            log.exception("Discovery job %s failed", kv)
            await asyncio.to_thread(self.repo.kv_set, kv, {"at": dt.datetime.now(UTC).isoformat(), "error": f"{type(e).__name__}: {str(e)[:150]}"})
            if job_id:
                await asyncio.to_thread(self.repo.job_update, job_id, state="failed", result={"error": str(e)[:200]})
            return {"error": str(e)}
        finally:
            setattr(self, flag, False)

    # ---- stream: GDELT's 15-minute files -----------------------------------------------------------
    async def run_stream(self, job_id: int | None = None) -> dict:
        return await self._run_job("stream_running", "discovery_last_stream", self._stream, job_id)

    async def _stream(self, job_id: int | None) -> dict:
        """Read the quarter hours not read yet (at most discovery.gdelt_files of them, English and translated), count every publisher
        in them, and promote those seen often enough."""
        last = self.repo.kv_get("gdelt_last_stamp") or ""
        todo = [t for t in gdelt.stamps(dt.datetime.now(UTC), int(self.settings.get("discovery.gdelt_files"))) if t > last][::-1]
        agg: dict[str, dict] = {}
        files = missing = 0
        async with self.http_factory() as http:
            for i, stamp in enumerate(todo, 1):
                await self._step(job_id, f"GDELT world news stream: {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]} UTC", i, len(todo))
                for translated in (False, True):
                    try:
                        mentions, events = await gdelt.quarter(http, stamp, translated)
                    except (httpx.HTTPError, zipfile.BadZipFile, IndexError):
                        mentions, events = [], []
                    if not mentions:
                        missing += 1
                        continue
                    files += 1
                    for dom, d in gdelt.parse(mentions, events, self.fips).items():
                        a = agg.setdefault(dom, {"domain": dom, "hits": 0, "files": 0, "focus": {}, "language": d["language"], "via": "gdelt",
                                                 "example": d["example"][:300], "homepage": f"https://{dom}/"})
                        a["hits"] += d["articles"]
                        a["files"] += 1
                        for c, k in d["focus"].items():
                            if c:
                                a["focus"][c] = a["focus"].get(c, 0) + k
                    await asyncio.sleep(0.3)
                last = max(last, stamp)
        await asyncio.to_thread(self.repo.web_hits_merge, list(agg.values()))
        await asyncio.to_thread(self.repo.kv_set, "gdelt_last_stamp", last)
        promoted = await self._promote_web_hits()
        if promoted:
            self.bus.publish({"type": "discovery", "promoted": promoted})
        return {"files": files, "missing": missing, "publishers": len(agg), "languages": len({a["language"] for a in agg.values()}), "promoted": promoted}

    # ---- editions: Google News country editions' section pages -----------------------------------------
    def editions_wanted(self) -> list[str]:
        """The editions in the languages collected (English only unless the owner chose more)."""
        if self.settings.get("sources.all_languages"):
            return list(websearch.EDITIONS)
        langs = set(self.languages())
        return [e for e in websearch.EDITIONS if e.split("-")[0] in langs]

    def plan_pages(self, n: int) -> list[dict]:
        """The next pages of the sweep: every edition's Top stories, then every edition's World page, then each one's national page, and so on."""
        eds = self.editions_wanted()
        pages = [(ed, sec) for sec in websearch.SECTIONS for ed in eds]
        self._next_cursor = 0
        if not pages:
            return []
        dead = set(self.repo.kv_get("gnews_dead") or [])
        cur = int(self.repo.kv_get("editions_cursor") or 0) % len(pages)
        out: list[dict] = []
        k = cur
        while len(out) < n and k < cur + len(pages):
            ed, sec = pages[k % len(pages)]
            k += 1
            if f"{ed}|{sec}" not in dead:
                out.append({"edition": ed, "section": sec, "label": f"Google News {ed} {sec.title() if sec != 'TOP' else 'Top stories'}"})
        self._next_cursor = k % len(pages)
        return out

    async def run_editions(self, job_id: int | None = None) -> dict:
        fn = self._editions_web if self.settings.get("discovery.gnews_mode") == "web" else self._editions_rss
        return await self._run_job("editions_running", "discovery_last_editions", fn, job_id)

    def plan_geo(self, n: int) -> list[dict]:
        """Local pages for cities of 100,000 people or more, those in the countries with the fewest sources and the fewest stories first;
        a city is asked again after 14 days. A city is asked in its country's English edition if there is one, else in its first edition."""
        if n <= 0 or not self.gaz:
            return []
        done = self.repo.kv_get("geo_done") or {}
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=14)).isoformat()
        counts = self.repo.country_source_counts()
        stories = self.repo.place_story_counts(7)
        by_cc: dict[str, list[str]] = {}
        for ed in self.editions_wanted() or ["en-US"]:
            by_cc.setdefault(ed.split("-")[1], []).append(ed)
        cities = [p for p in self.gaz.places.values() if p.level == "city" and p.population >= 100_000]
        cities.sort(key=lambda p: (counts.get(p.code.split("-")[0], 0), stories.get(p.code, 0), -p.population))
        out: list[dict] = []
        for p in cities:
            if len(out) >= n:
                break
            if done.get(p.code, "") > cutoff:
                continue
            cc = p.code.split("-")[0]
            eds = by_cc.get(cc)
            ed = next((e for e in eds if e.startswith("en-")), eds[0]) if eds else "en-US"
            parent = self.gaz.get(p.parent) if p.parent else None
            within = parent.name if parent and parent.level == "state" and eds else self._country_name(cc)
            out.append({"edition": ed, "section": "", "geo": f"{p.name}, {within}", "label": f"local news for {p.name}", "place": p.code, "country": cc})
        return out

    async def _editions_rss(self, job_id: int | None) -> dict:
        """Google News RSS: the sweep's next section pages, and (a third of each run) local pages for cities. Publishers on them are
        counted for discovery; their stories from publishers already collected are stored (Google's links opened within a budget)."""
        n = int(self.settings.get("discovery.edition_pages"))
        n_geo = n // 3 if self.settings.get("discovery.gnews_local") else 0
        plan = await asyncio.to_thread(self.plan_pages, n - n_geo)
        next_cursor = self._next_cursor
        plan += await asyncio.to_thread(self.plan_geo, n_geo)
        agg: dict[str, dict] = {}
        dead = set(self.repo.kv_get("gnews_rss_dead") or [])
        geo_done: dict[str, str] = self.repo.kv_get("geo_done") or {}
        pages, notes, items_all = 0, [], []
        prio = {"NATION": 1, "TOP": 2}
        async with self.http_factory() as http:
            for i, pg in enumerate(plan, 1):
                await self._step(job_id, pg["label"], i, len(plan))
                url = websearch.rss_url(pg["edition"], geo=pg["geo"]) if pg.get("geo") else websearch.rss_url(pg["edition"], pg["section"])
                try:
                    items = await websearch.rss(http, url)
                except websearch.WebRateLimited as e:
                    notes.append(f"{e}. Stopped early; the next run continues.")
                    next_cursor = None
                    break
                except httpx.HTTPError as e:
                    notes.append(f"{pg['label']}: {type(e).__name__}")
                    continue
                finally:
                    await asyncio.sleep(self.page_sleep)
                if pg.get("geo"):
                    geo_done[pg["place"]] = dt.datetime.now(UTC).isoformat()
                if items is None:
                    if not pg.get("geo"):
                        dead.add(f"{pg['edition']}|{pg['section']}")
                    continue
                pages += 1
                hl, gl = pg["edition"].split("-")
                nation = [pg["country"]] if pg.get("geo") else ([gl] if pg["section"] == "NATION" else [])
                per: dict[str, dict] = {}
                for r in items:
                    d = site_of(r["site_url"])
                    if d:
                        per.setdefault(d, {"name": r["name"], "url": r["site_url"], "n": 0})["n"] += 1
                    items_all.append({**r, "priority": 0 if pg.get("geo") else prio.get(pg["section"], 3)})
                for d, v in per.items():
                    a = agg.setdefault(d, {"domain": d, "name": v["name"], "hits": 0, "queries": 0, "language": hl, "homepage": v["url"], "via": "google_news",
                                           "example": pg["label"], "editions": [], "nation_editions": []})
                    a["hits"] += v["n"]
                    a["queries"] += 1
                    if pg["edition"] not in a["editions"]:
                        a["editions"].append(pg["edition"])
                    for c in nation:
                        if c not in a["nation_editions"]:
                            a["nation_editions"].append(c)
            stored: dict = {}
            if items_all and self.settings.get("discovery.edition_articles"):
                stored = await self._store_gnews(items_all, http)
        await asyncio.to_thread(self.repo.web_hits_merge, list(agg.values()))
        if next_cursor is not None:
            await asyncio.to_thread(self.repo.kv_set, "editions_cursor", next_cursor)
        await asyncio.to_thread(self.repo.kv_set, "gnews_rss_dead", sorted(dead))
        await asyncio.to_thread(self.repo.kv_set, "geo_done", dict(sorted(geo_done.items(), key=lambda kv: kv[1])[-6000:]))
        promoted = await self._promote_web_hits()
        total = len(self.editions_wanted()) * len(websearch.SECTIONS)
        return {"mode": "rss", "pages": pages, "local_pages": sum(1 for p in plan if p.get("geo")), "cards": len(items_all), "publishers": len(agg),
                "promoted": promoted, **stored, "sweep_position": f"{next_cursor or 0}/{total}", "notes": notes[:20]}

    async def _store_gnews(self, items: list[dict], http: httpx.AsyncClient) -> dict:
        """Store the stories of Google News items whose publisher is already collected, as that publisher's articles with its own link.
        Google's link is opened only for a story the app does not have yet (not looked up before, and no article of that publisher with the
        same headline in the last three days), at most discovery.gnews_decode_max per run, one a second."""
        if not self.on_cards:
            return {}
        known = await asyncio.to_thread(self.repo.sources_for_sites, list({site_of(i["site_url"]) for i in items if i.get("site_url")}))
        cand: dict[str, dict] = {}
        for i in items:
            site = site_of(i.get("site_url") or "")
            if i.get("gid") and site in known and i["gid"] not in cand:
                cand[i["gid"]] = {**i, "source_key": known[site]["source_key"]}
        if not cand:
            return {"google_items_known_publishers": 0}
        looked = await asyncio.to_thread(self.repo.gnews_links_seen, list(cand))
        todo = [c for g, c in cand.items() if g not in looked]
        have = await asyncio.to_thread(self.repo.recent_titles, [(c["source_key"], c["title"]) for c in todo], 3)
        had = [c for c in todo if (c["source_key"], c["title"].strip().lower()) in have]
        todo = [c for c in todo if (c["source_key"], c["title"].strip().lower()) not in have]
        todo.sort(key=lambda c: (c["priority"], -(c["published"].timestamp() if c.get("published") else 0)))
        budget = int(self.settings.get("discovery.gnews_decode_max"))
        cards, rows, notes = [], [(c["gid"], None) for c in had], []
        for c in todo[:budget]:
            try:
                url = await websearch.decode(http, c["gid"])
            except websearch.WebRateLimited as e:
                notes.append(f"Opening Google's links: {e}; the rest wait for the next run.")
                break
            except httpx.HTTPError:
                url = None
            rows.append((c["gid"], url))
            if url:
                cards.append({"url": url, "name": c["name"], "title": c["title"], "published": c.get("published")})
            await asyncio.sleep(self.decode_sleep)
        await asyncio.to_thread(self.repo.gnews_links_add, rows)
        res = await self.on_cards(cards) if cards else {"articles_new": 0}
        return {"google_items_known_publishers": len(cand), "already_had": len(looked) + len(had), "links_opened": len(rows) - len(had),
                "links_waiting": max(0, len(todo) - budget), **res, **({"decode_notes": notes} if notes else {})}

    async def _editions_web(self, job_id: int | None) -> dict:
        """Read the planned pages. Every publisher on them is counted (those on several pages become candidates); with
        discovery.edition_articles on, each story card from a publisher already collected is stored as that publisher's article."""
        plan = await asyncio.to_thread(self.plan_pages, int(self.settings.get("discovery.edition_pages")))
        next_cursor = self._next_cursor
        agg: dict[str, dict] = {}
        dead = set(self.repo.kv_get("gnews_dead") or [])
        menus: dict[str, dict] = self.repo.kv_get("gnews_sections") or {}
        week_ago = (dt.datetime.now(UTC) - dt.timedelta(days=7)).isoformat()
        pages, cards_n, notes = 0, 0, []
        cards_all: list[dict] = []

        def count(cards: list[dict], pg: dict, edition: str) -> None:
            hl, gl = edition.split("-")
            nation = [gl] if pg.get("section") == "NATION" else []
            per: dict[str, dict] = {}
            for r in cards:
                d = site_of(r["url"])
                if d:
                    per.setdefault(d, {"name": r["name"], "n": 0})["n"] += 1
            for d, v in per.items():
                a = agg.setdefault(d, {"domain": d, "name": v["name"], "hits": 0, "queries": 0, "language": hl, "homepage": f"https://{d}/", "via": "google_news",
                                       "example": pg["label"], "editions": [], "nation_editions": []})
                a["hits"] += v["n"]
                a["queries"] += 1
                if edition not in a["editions"]:
                    a["editions"].append(edition)
                for c in nation:
                    if c not in a["nation_editions"]:
                        a["nation_editions"].append(c)

        async with self.http_factory() as http:
            robots_on = bool(self.settings.get("fetch.robots"))
            rules = ""
            try:
                if robots_on:
                    rules = await websearch.robots(http)
            except httpx.HTTPError as e:
                rules = None
                notes.append(f"robots.txt: {type(e).__name__}")
            if rules is None:
                return {"pages": 0, "publishers": 0, "promoted": 0, "notes": notes + ["Google News' robots.txt could not be read, so no page was read"]}
            ua = self.fetcher.token

            async def fetch(url: str, label: str) -> str | None:
                if robots_on and not websearch.robots_allows(rules, url, ua):
                    notes.append(f"{label}: not allowed by Google News' robots.txt, skipped")
                    return None
                try:
                    return await websearch.page(http, url)
                finally:
                    await asyncio.sleep(self.page_sleep)

            for i, pg in enumerate(plan, 1):
                ed, sec = pg["edition"], pg["section"]
                await self._step(job_id, pg["label"], i, len(plan))
                try:
                    menu = menus.get(ed)
                    if not menu or menu.get("at", "") < week_ago:          # the section ids come from the edition's own menu, read weekly
                        home = await fetch(websearch.home_url(ed), f"Google News {ed} front page")
                        if home is None:
                            dead.update(f"{ed}|{s}" for s in websearch.SECTIONS)
                            continue
                        menu = menus[ed] = {"at": dt.datetime.now(UTC).isoformat(), "ids": websearch.parse_sections(home)}
                        cards = websearch.parse_cards(home)
                        del home
                        count(cards, {"label": f"Google News {ed} front page"}, ed)
                        cards_all.extend(cards)
                        pages += 1
                    tid = menu["ids"].get(sec)
                    if tid:
                        html = await fetch(websearch.topic_url(ed, tid), pg["label"])
                    elif sec == "TOP":                                      # a menu we cannot read (not English): the front page instead
                        html = await fetch(websearch.home_url(ed), pg["label"])
                    else:
                        dead.add(f"{ed}|{sec}")                             # no such section in this edition's menu
                        continue
                except websearch.WebRateLimited as e:
                    notes.append(f"{e}. Stopped early; the next run continues.")
                    next_cursor = None
                    break
                except httpx.HTTPError as e:
                    notes.append(f"{pg['label']}: {type(e).__name__}")
                    continue
                if html is None:
                    continue
                cards = websearch.parse_cards(html)
                if not cards and len(html) > 50000:
                    notes.append(f"{pg['label']}: no story cards found; Google may have changed the page")
                del html
                pages += 1
                count(cards, pg, ed)
                cards_all.extend(cards)
        cards_n = len(cards_all)
        await asyncio.to_thread(self.repo.web_hits_merge, list(agg.values()))
        if next_cursor is not None:
            await asyncio.to_thread(self.repo.kv_set, "editions_cursor", next_cursor)
        await asyncio.to_thread(self.repo.kv_set, "gnews_dead", sorted(dead))
        await asyncio.to_thread(self.repo.kv_set, "gnews_sections", menus)
        promoted = await self._promote_web_hits()
        stored: dict = {}
        if self.on_cards and cards_all and self.settings.get("discovery.edition_articles"):
            stored = await self.on_cards(cards_all)
        total = len(self.editions_wanted()) * len(websearch.SECTIONS)
        return {"mode": "web", "pages": pages, "cards": cards_n, "publishers": len(agg), "promoted": promoted, **stored,
                "sweep_position": f"{next_cursor or 0}/{total}", "notes": notes[:20]}

    # ---- wikidata: each country's best-known outlets --------------------------------------------------
    async def run_wikidata(self, job_id: int | None = None) -> dict:
        return await self._run_job("wd_running", "discovery_last_wikidata", self._wikidata_countries, job_id)

    async def _wikidata_countries(self, job_id: int | None) -> dict:
        done: dict[str, str] = self.repo.kv_get("wd_country_done") or {}
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=30)).isoformat()
        counts = await asyncio.to_thread(self.repo.country_source_counts)
        target = self._caps()[0]
        # every country is asked, the thinnest first: a country with many sources can still lack its best-known outlets
        todo = [c for c in sorted(self._valid_codes(), key=lambda c: (counts.get(c, 0) >= target, counts.get(c, 0), c)) if done.get(c, "") < cutoff]
        todo = todo[: int(self.settings.get("discovery.wikidata_countries"))]
        add, _, _ = await self._adder()
        asked = added = 0
        notes: list[str] = []
        async with self.http_factory() as http:
            for i, cc in enumerate(todo, 1):
                await self._step(job_id, f"Wikidata: outlets of {self._country_name(cc)}", i, len(todo))
                try:
                    outs = await wikidata_feeds.country_outlets(http, cc)
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as e:
                    notes.append(f"{cc}: {type(e).__name__} {str(e)[:60]}")
                    await asyncio.sleep(self.wd_sleep * 5)
                    continue
                asked += 1
                for o in outs:
                    lang = (o["language"] or "").lower()
                    domain = host_of(o["site"])
                    if not domain or not self.lang_ok(lang):
                        continue
                    rec = {"name": o["name"][:120], "domain": domain, "country": cc, "type": source_type_for(domain), "language": lang or "en", "origin": "wikidata",
                           "mc_id": None, "homepage": o["site"], "media_type": None, "stories_per_week": None,
                           "note": f"Wikidata {o['qid']}: a known outlet of {self._country_name(cc)} ({o['links']} Wikipedia articles about it)"}
                    if await asyncio.to_thread(add, rec):
                        added += 1
                done[cc] = dt.datetime.now(UTC).isoformat()
                await asyncio.to_thread(self.repo.kv_set, "wd_country_done", done)
                await asyncio.sleep(self.wd_sleep)
        left = sum(1 for c in self._valid_codes() if done.get(c, "") < cutoff)
        return {"countries": asked, "added": added, "countries_left": left, "notes": notes}

    # ---- balance ----------------------------------------------------------------------------------
    async def run_rebalance(self) -> dict:
        cap = self._caps()[1] if self.settings.get("discovery.rebalance") else 10 ** 9      # parking off: everything parked comes back
        res = await asyncio.to_thread(self.repo.rebalance, cap)
        if self.upgrader:
            try:
                res["upgrade"] = await self.upgrader.run()
            except Exception as e:                                  # noqa: BLE001 - one step of the balance; must not stop the rest
                log.exception("Reliability upgrade failed")
                res["upgrade"] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
        await asyncio.to_thread(self.repo.kv_set, "discovery_last_rebalance", {"at": dt.datetime.now(UTC).isoformat(), **res})
        return res

    def coverage(self) -> dict:
        """How evenly the registry covers the world: countries with any working source, under the target, at the cap."""
        target, cap = self._caps()
        counts = self.repo.country_source_counts()
        valid = self._valid_codes()
        have = {c: n for c, n in counts.items() if c in valid}
        return {"countries": len(valid), "with_sources": len(have), "without": len(valid) - len(have), "under_target": sum(1 for c in valid if have.get(c, 0) < target),
                "at_cap": sum(1 for n in have.values() if n >= cap), "target": target, "cap": cap, "unknown_country": counts.get("XX", 0),
                "top": sorted(have.items(), key=lambda kv: -kv[1])[:8]}
