"""Collect: seed the starter registry, poll feeds, classify, geotag, cluster into stories, flag breaking news.

The clustering here compares headline words (cheap, no model). Phase 3 replaces it with embeddings; the
rest of the pipeline stays the same."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import time
from collections import Counter, deque
from pathlib import Path

from app.dao.fetch import solver as flare
from app.dao.feeds import community, homepage, sitemap
from app.dao.feeds.rss import parse_feed
from app.services.anchors import anchors, different_events, rarity_bar
from app.services.citations import cited_domains
from app.services.merge import routine_item
from app.services.textutil import canonical_url, clean_leads, detect_lang, fingerprint, is_media_url, norm_title, title_key

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
DATA = Path(__file__).resolve().parent.parent / "data"


def parse_sources_file(path: Path | None = None) -> tuple[list[dict], list[dict]]:
    sources, feeds = [], []
    for raw in (path or DATA / "sources.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if parts[0] == "@source":
            sources.append(dict(zip(("key", "name", "domain", "country", "type", "scope"), parts[1:7])))
        elif parts[0] == "feed":
            key, url, topics, places, kind = (parts + ["", "", "", ""])[1:6]
            mode = "inherit" if not places else "none" if places == "-" else "explicit"
            feeds.append({"source_key": key, "url": url, "topics": [t.strip() for t in topics.split(",") if t.strip()],
                          "places": [] if mode != "explicit" else [p.strip() for p in places.split(",") if p.strip()], "place_mode": mode,
                          "kind": kind or "rss"})
    return sources, feeds


def effective_time(published: dt.datetime, now: dt.datetime) -> dt.datetime:
    """When a report really appeared. A date without a time ("12 Nov 2026" read as 00:00:00 UTC) says only the day: it is taken as
    the time the report was seen when that is within a day and a half of it, since a feed shows a new report soon after it is published.
    A time in the future is the time of the check."""
    if published.tzinfo is None:
        published = published.replace(tzinfo=dt.timezone.utc)
    published = min(published, now)
    if published.hour == published.minute == published.second == published.microsecond == 0 and now - published < dt.timedelta(hours=36):
        return now
    return published


def starts_new_development(pub: dt.datetime, first_seen: dt.datetime, last_update: dt.datetime, max_gap: dt.timedelta, max_span: dt.timedelta) -> bool:
    """A report is a later event of the story's thread, not part of the story, if the story had no report for ``max_gap`` before it
    or it comes more than ``max_span`` after the story's first report (or, being older, more than ``max_gap`` before it)."""
    if pub >= first_seen:
        return pub - last_update > max_gap or pub - first_seen > max_span
    return first_seen - pub > max_gap


def match_story(tokens: list[str], candidates: list[dict], veto=None) -> int | None:
    """Pick the candidate story whose headline words overlap enough with ``tokens``. ``veto(candidate)`` True drops a candidate
    (its headline names other things: a different event worded alike)."""
    a = set(tokens)
    best, best_sim = None, 0.0
    for c in candidates:
        b = set(c["tokens"])
        shared = len(a & b)
        if shared < 2 or not a or not b:
            continue
        sim = shared / min(len(a), len(b))
        jac = shared / len(a | b)
        ok = (shared >= 3 and sim >= 0.6 and jac >= 0.4) or (shared >= 3 and jac >= 0.5)
        if ok and sim + jac > best_sim and not (veto and veto(c)):
            best, best_sim = c["id"], sim + jac
    return best


QUIET_FACTOR, BUSY_FACTOR, MIN_INTERVAL_S, MAX_QUIET_S, MAX_FAIL_S = 1.5, 0.7, 300, 7200, 21600


def interval_bounds(base_s: int) -> tuple[int, int]:
    """(shortest, longest) time between checks of a feed that answers: a busy feed down to a sixth of the base interval (never under
    5 minutes), a quiet one up to two hours (or the base interval, if the owner set it longer)."""
    return max(MIN_INTERVAL_S, base_s // 6), max(MAX_QUIET_S, base_s)


def next_interval(current_s: int, base_s: int, found_new: bool, ok: bool, kind: str | None = None) -> int:
    """Seconds until a feed is checked again. A feed that answered and had something new is checked sooner (x0.7), one that answered with
    nothing new later (x1.5), within interval_bounds; one that failed at twice its interval, up to six hours. Nothing is ever dropped from
    the schedule: the longest wait is two hours for a quiet feed and six hours for a failing one, and the daily re-analysis
    (feeds_reanalyse) resets every feed's interval from its last week's activity."""
    lo, hi = interval_bounds(base_s)
    if ok:
        iv = int(min(hi, max(lo, current_s * (BUSY_FACTOR if found_new else QUIET_FACTOR))))
    else:
        iv = int(min(MAX_FAIL_S, max(current_s, base_s) * 2))
    if kind == "homepage":
        iv = max(1800, iv)                                 # a whole page, not a feed: at most every 30 minutes
    return max(MIN_INTERVAL_S, iv)


class _Result:
    """The parts of a fetch result the poller uses, for feeds read through an API."""
    def __init__(self, status: int, body, error: str | None):
        self.status, self.body, self.error = status, body, error
        self.final_url = self.etag = self.last_modified = None


class IngestService:
    def __init__(self, repo, settings, taxonomy, gazetteer, fetcher, governor, bus):
        self.repo, self.settings, self.tax, self.gaz = repo, settings, taxonomy, gazetteer
        self.fetcher, self.governor, self.bus = fetcher, governor, bus
        self._lock = asyncio.Lock()
        self._db_gate = asyncio.Semaphore(1)     # fetching runs in parallel; story assignment must be one writer at a time or two feeds create duplicate stories
        self.running = False
        self._merging = False                    # a merge pass is running (it runs next to the polling, see run_merge)
        self._unmerged = 0                       # reports stored since the last merge pass started
        self._merge_done_at = 0.0                # monotonic time the last merge pass finished
        self._merge_force = False                # a pass found more waiting than it could take: run the next one at once
        self.timing: dict[str, float] = {}
        self.after_cycle = None                  # async callable run after each refresh (story merging)
        self.social = None                       # SocialService: reads the feeds of kind bluesky, mastodon, reddit and x
        self._partitions_ready: set[dt.date] = set()
        self._frequent: tuple[set[str], float] = (set(), 0.0)     # headline words too common to tie two reports together, and when measured
        self._names: dict[int, set[str]] = {}                    # anchors.anchors() of candidate stories' headlines, by story id

    def frequent_words(self) -> set[str]:
        """Words used by more stories of the last 72 hours than the rarity bar, measured at most every 30 minutes."""
        import time
        words, at = self._frequent
        if time.monotonic() - at > 1800 or not at:
            try:
                words, _n = self.repo.frequent_tokens(72, rarity_bar)
            except Exception:                                    # noqa: BLE001 - without the list, every shared word counts as rare
                log.exception("Could not measure frequent headline words")
            self._frequent = (words, time.monotonic())
        return words

    def names_of(self, story_id: int, title: str) -> set[str]:
        n = self._names.get(story_id)
        if n is None:
            if len(self._names) > 60_000:
                self._names.clear()
            n = self._names[story_id] = anchors(title, self.gaz)
        return n

    # ---- setup -----------------------------------------------------------------------------------
    def seed(self) -> None:
        sources, feeds = parse_sources_file()
        keys = {s["key"] for s in sources}
        for s in sources:
            self.repo.upsert_source(s)
        bad = 0
        for f in feeds:
            if f["source_key"] not in keys:
                log.warning("Feed %s references unknown source %s", f["url"], f["source_key"])
                continue
            for t in f["topics"]:
                if t not in self.tax.topics:
                    bad += 1
                    log.warning("Unknown topic hint %s on %s", t, f["url"])
            self.repo.upsert_feed({**f, "interval": int(self.settings.get("refresh.news_minutes")) * 60})
        if bad:
            log.warning("%d unknown topic hints in sources.txt", bad)
        self.repo.topic_prefs_seed({c: t.default_enabled for c, t in self.tax.topics.items() if t.depth >= 2})
        # No shortcuts are created for you: favourites are only what you add (owner's request, 1 Nov 2026).
        log.info("Registry: %d sources, %d feeds", len(sources), len(feeds))

    def enabled_topics(self) -> set[str]:
        prefs = self.repo.topic_prefs()
        return {c for c, on in prefs.items() if on}

    # ---- one cycle -------------------------------------------------------------------------------
    async def run_cycle(self, job_id: int | None = None) -> dict:
        if self._lock.locked():
            return {"skipped": "already running"}
        async with self._lock:
            self.running = True
            try:
                return await self._cycle(job_id)
            finally:
                self.running = False

    async def _cycle(self, job_id: int | None) -> dict:
        t0 = dt.datetime.now(UTC)
        paused = bool(await asyncio.to_thread(self.repo.kv_get, "ingest_paused", False))
        self.fetcher.configure(self.settings.get("refresh.feed_timeout_s"), self.settings.get("refresh.host_delay_s"))
        await self.fetcher.start()
        feeds = await asyncio.to_thread(self.repo.feeds_due, None if self.settings.get("sources.all_languages") else list(self.settings.get("sources.languages")))
        enabled = await asyncio.to_thread(self.enabled_topics)
        feeds = [f for f in feeds if self.tax.feed_active(f["topics"], enabled)]
        stats = {"feeds_total": len(feeds), "feeds_done": 0, "feeds_failed": 0, "articles_new": 0, "stories_new": 0, "paused": paused}
        if job_id:
            await asyncio.to_thread(self.repo.job_update, job_id, state="running", progress=stats)
        if paused or not feeds:
            if job_id:
                await asyncio.to_thread(self.repo.job_update, job_id, state="done", progress=stats, result=stats)
            return stats
        self.timing = {"fetch_s": 0.0, "stores": 0, "store_wait_s": 0.0, "store_hold_s": 0.0, "deferred": 0, "retried_ok": 0, "unusable_retried": 0}
        queue, retry = deque(feeds), deque()                        # feeds that timed out or were reset wait at the end of the queue for a second try
        gap = int(self.settings.get("refresh.merge_gap_s"))
        limit = {"n": self.governor.concurrency(), "at": time.monotonic()}

        async def one(f: dict, final: bool) -> None:
            try:
                res = await self._poll(f, final)
            except Exception as e:                          # one bad feed must never stop the cycle
                log.exception("Feed %s crashed: %s", f["url"], e)
                res = (0, 0, False)
            if res is None:
                retry.append(f)
                self.timing["deferred"] += 1
                return
            n, st, ok = res
            if final and f.get("_second_try") and ok:
                self.timing["retried_ok"] += 1
            stats["feeds_done"] += 1
            stats["feeds_failed"] += 0 if ok else 1
            stats["articles_new"] += n
            stats["stories_new"] += st
            self._unmerged += n
            if stats["feeds_done"] % 10 == 0 or stats["feeds_done"] == stats["feeds_total"]:
                self.bus.publish({"type": "progress", **stats})
                if job_id:
                    await asyncio.to_thread(self.repo.job_update, job_id, progress=dict(stats))

        async def worker(i: int) -> None:
            while queue or retry:
                if time.monotonic() - limit["at"] > 10:                       # the memory governor is asked again as the refresh goes on
                    limit["n"], limit["at"] = self.governor.concurrency(), time.monotonic()
                if i >= limit["n"]:
                    await asyncio.sleep(2)
                    continue
                if queue:
                    await one(queue.popleft(), False)
                elif retry:
                    f = retry.popleft()
                    f["_second_try"] = True
                    await one(f, True)

        done = asyncio.Event()

        async def merger() -> None:                                 # a pass in progress is never cancelled: it ends the refresh's wait
            while not done.is_set():
                try:
                    await asyncio.wait_for(done.wait(), 5)
                except asyncio.TimeoutError:
                    pass
                if not done.is_set() and self._merge_due(gap):
                    await self.run_merge(stats)

        mtask = asyncio.create_task(merger())
        try:
            await asyncio.gather(*(worker(i) for i in range(int(self.settings.get("refresh.concurrency")))))
        finally:
            done.set()
            await asyncio.gather(mtask, return_exceptions=True)
        while self._merging:
            await asyncio.sleep(0.5)
        if self._unmerged and self._merge_due(gap):
            await self.run_merge(stats)                            # reports not yet merged wait for the next pass at most refresh.merge_gap_s
        stats.update({k: round(v, 1) if isinstance(v, float) else v for k, v in self.timing.items()})
        stats["seconds"] = round((dt.datetime.now(UTC) - t0).total_seconds(), 1)
        if job_id:
            await asyncio.to_thread(self.repo.job_update, job_id, state="done", progress=stats, result=stats)
        self.bus.publish({"type": "refreshed", **stats})
        log.info("Cycle: %s", stats)
        return stats

    def _merge_due(self, gap_s: int) -> bool:
        """Reports are waiting and the last pass ended at least ``gap_s`` ago (at once if the last pass left a backlog)."""
        if self._merging or not self._unmerged or not self.after_cycle:
            return False
        return self._merge_force or time.monotonic() - self._merge_done_at >= gap_s

    async def run_merge(self, stats: dict | None = None) -> None:
        """One merge pass (translate, meaning vectors, merging, headlines, meaning check). It runs next to the polling: only its
        writes take the store gate, so feeds keep being fetched while it reads and compares."""
        if self._merging or not self.after_cycle:
            return
        self._merging, self._merge_force = True, False
        self._unmerged = 0
        t0 = time.monotonic()
        try:
            res = await self.after_cycle()
        except Exception:                                       # noqa: BLE001 - merging is an improvement; a failure must not fail the refresh
            log.exception("Story merge pass failed")
            self._unmerged = max(self._unmerged, 1)
            return
        finally:
            self._merging = False
            self._merge_done_at = time.monotonic()
        secs = round(time.monotonic() - t0, 1)
        if int((res or {}).get("embedded") or 0) >= 3000:        # more than one pass can take: go on straight away
            self._merge_force, self._unmerged = True, max(self._unmerged, 1)
        if stats is not None:
            stats["merge"] = res
            stats["merged_total"] = stats.get("merged_total", 0) + int((res or {}).get("merged") or 0)
            stats["merge_passes"] = stats.get("merge_passes", 0) + 1
            stats["merge_s"] = round(stats.get("merge_s", 0.0) + secs, 1)
        log.info("Merge pass (%.1f s): %s", secs, res)

    async def merge_tick(self) -> None:
        """Called by the scheduler between refreshes: merge what is waiting once the gap has passed."""
        if not self.running and self._merge_due(int(self.settings.get("refresh.merge_gap_s"))):
            await self.run_merge()

    async def _store(self, f: dict, items: list[dict]) -> tuple[int, int]:
        """process_items under the store gate, timed (how long feeds wait for the one writer, and how long it holds the gate)."""
        t0 = time.monotonic()
        async with self._db_gate:
            t1 = time.monotonic()
            out = await asyncio.to_thread(self.process_items, f, items)
        self.timing["stores"] = self.timing.get("stores", 0) + 1
        self.timing["store_wait_s"] = self.timing.get("store_wait_s", 0.0) + (t1 - t0)
        self.timing["store_hold_s"] = self.timing.get("store_hold_s", 0.0) + (time.monotonic() - t1)
        return out

    async def _read(self, f: dict, r) -> tuple[list[dict], bool, int]:
        """The items of a fetched page or feed: (items, whether the content was recognised as a feed, sitemap or page of links, routed count)."""
        routed = 0
        if f.get("kind") == "homepage":
            links = await asyncio.to_thread(homepage.article_links, r.body.decode("utf-8", "replace"), r.final_url or f["url"])
            hashes = {hashlib.sha1(canonical_url(x["url"]).encode()).hexdigest(): x for x in links}
            fresh = await asyncio.to_thread(self.repo.homepage_seen_update, f["id"], list(hashes))
            items, recognised = [hashes[h] for h in hashes if h in fresh], bool(links)
        elif f.get("kind") == "sitemap" and sitemap.is_index(r.body):
            items, recognised = await sitemap.read(self.fetcher, f["url"], r.body)
        elif f.get("kind") == "community":
            items, recognised = await asyncio.to_thread(community.parse, r.body, r.final_url or f["url"], f.get("source_domain") or "")
            items, routed = await self._route_to_publishers(items)
            recognised = recognised or routed > 0
        else:
            items, recognised = await asyncio.to_thread(parse_feed, r.body, r.final_url or f["url"])
        return items, recognised, routed

    async def _poll(self, f: dict, final: bool = True) -> tuple[int, int, bool] | None:
        """Check one feed. ``final=False`` (the first try): a timeout or reset is not recorded or retried at once; None is returned and the
        feed goes to the end of the refresh's queue for a second try."""
        base = int(self.settings.get("refresh.news_minutes")) * 60
        items: list[dict] = []
        empty_ok = False
        new_articles = new_stories = routed = 0
        t_fetch = time.monotonic()
        if f.get("kind") in ("bluesky", "mastodon", "reddit", "x", "youtube"):
            if not self.social:
                return 0, 0, False
            posts, fine, note = await self.social.fetch(f)
            r = _Result(200 if fine else 0, None, None if fine else note)
            if posts:
                items = posts
                new_articles, new_stories = await self._store(f, items)
            empty_ok = fine
        else:
            acc = {"accept": "text/html,*/*;q=0.8"} if f.get("kind") == "homepage" else {}
            r = await self.fetcher.get(f["url"], f["etag"], f["last_modified"], defer_transient=not final, solve=True, **acc)
            if getattr(r, "transient", False):
                self.timing["fetch_s"] += time.monotonic() - t_fetch
                return None
        self.timing["fetch_s"] += time.monotonic() - t_fetch
        if r.body is not None and r.status == 200 and r.body and not r.error:
            items, recognised, routed = await self._read(f, r)
            acc2 = {"accept": "text/html,*/*;q=0.8"} if f.get("kind") == "homepage" else {}
            if not items and not recognised and not getattr(r, "impersonated", False) and not getattr(r, "solved", False) \
                    and f.get("kind") != "community" and self.fetcher.impersonate_profile():
                # a 200 that is not a feed, sitemap or page of links (a challenge or consent page, an empty shell): the full content may
                # come with a browser's fingerprint
                alt = await self.fetcher.get(f["url"], f["etag"], f["last_modified"], impersonated=True, **acc2)
                if alt.impersonated and alt.status == 200 and alt.body:
                    self.timing["unusable_retried"] += 1
                    r = alt
                    items, recognised, routed = await self._read(f, r)
            if not items and not recognised and not getattr(r, "solved", False) and f.get("kind") != "community" \
                    and self.fetcher.solver_config() and flare.looks_challenged(r.body):
                # still a challenge page: last, a real browser (FlareSolverr), only for pages that say they are a challenge
                alt = await self.fetcher.get(f["url"], solved=True, defer_transient=not final, **acc2)
                if getattr(alt, "transient", False):
                    return None                             # the solver is busy: this feed is checked again at the end of the queue
                if alt.solved and alt.body:
                    self.timing["unusable_retried"] += 1
                    r = alt
                    items, recognised, routed = await self._read(f, r)
            r.body = None
            empty_ok = recognised and not items
            if items:
                new_articles, new_stories = await self._store(f, items)
            new_articles += routed
        ok = r.status in (200, 304) and not r.error and not (r.status == 200 and not items and not empty_ok)
        interval = next_interval(f["poll_interval_s"], base, bool(new_articles), ok, f.get("kind"))
        fails = (f["fail_count"] + 1) if not ok else 0
        state = "dormant" if fails >= 20 else "degraded" if fails >= 5 else "active"
        err = r.error or (None if ok else "No items found in the response")
        await asyncio.to_thread(self.repo.feed_polled, f["id"], status=r.status, error=err, new_items=new_articles, items_seen=len(items),
                                etag=r.etag, last_modified=r.last_modified, interval_s=interval, ok=ok, state=state)
        return new_articles, new_stories, ok

    async def ingest_cards(self, cards: list[dict]) -> dict:
        """Story cards read on Google News pages: each one from a publisher in the collection is stored as that publisher's article (its
        own link, headline and time; no summary). Cards from publishers not collected are only counted by discovery. A story already
        stored from the publisher's feed has the same address and is not stored twice."""
        from app.services.discovery import site_of
        by_site: dict[str, list[dict]] = {}
        for c in cards:
            s = site_of(c["url"])
            if s and c.get("title"):
                by_site.setdefault(s, []).append({"title": c["title"][:500], "url": c["url"], "lead": "", "published": c.get("published")})
        feeds = await asyncio.to_thread(self.repo.sources_for_sites, list(by_site))
        enabled = await asyncio.to_thread(self.enabled_topics)
        new = 0
        for site, f in feeds.items():
            if not self.tax.feed_active(f["topics"], enabled):
                continue
            async with self._db_gate:
                n, _s = await asyncio.to_thread(self.process_items, f, by_site[site])
            new += n
        self._unmerged += new
        return {"cards_known_publishers": sum(len(by_site[s]) for s in feeds), "articles_new": new}     # merged and checked by the next refresh's pass

    async def _route_to_publishers(self, items: list[dict]) -> tuple[list[dict], int]:
        """Of a community site's items, store those from collected publishers as the publisher's own articles (so a story is credited to
        its outlet, and the outlet's own feed does not find it already taken); return the rest, for the community source, and the count."""
        from app.services.discovery import site_of
        by_site: dict[str, list[dict]] = {}
        for it in items:
            by_site.setdefault(site_of(it["url"]), []).append(it)
        feeds = await asyncio.to_thread(self.repo.sources_for_sites, [s for s in by_site if s])
        n = 0
        for site, fd in feeds.items():
            async with self._db_gate:
                a, _s = await asyncio.to_thread(self.process_items, fd, by_site.pop(site))
            n += a
        return [it for v in by_site.values() for it in v], n

    @staticmethod
    def _home_fallback(feed: dict, topics: dict) -> bool:
        """An article with no place of its own is 'about' the outlet's country, except where that would be wrong:
        specialist or company publications and sport, entertainment and games feeds cover the world, not the home country."""
        if feed["source_type"] in ("specialist", "company") or "sport" in topics:
            return False
        return not any(h.split(".")[0] in ("sport", "arts") for h in feed["topics"])

    TAG_VERSION = "2026-11-04a"      # topics v2.2: tighter aliases for generic words (policing, markets, war, clubs, ...)
    THREADS_VERSION = "2026-11-02a"
    ROUTINE_VERSION = "2026-11-02a"  # bump when merge.ROUTINE changes      # bump when the classifier or gazetteer changes, so stored articles are tagged again

    def tag(self, feed: dict, title: str, lead: str) -> tuple[dict[str, float], set[str]]:
        """Topics and places (with ancestors) for one article."""
        topics = self.tax.classify(title, lead, feed["topics"], country=feed["source_country"])
        # the places the report is about, not every place it mentions (Gazetteer.detect_focus)
        places = self.gaz.detect_focus(title, lead, feed["source_country"] if feed["source_country"] != "XX" else "")
        if feed["place_mode"] == "explicit":
            # a feed's own place (a city paper's feed) applies unless the article is about somewhere else: a Chennai paper's general feed
            # also carries national and world news, and sport. It applies when the article names no place, or only places inside it.
            for p in feed["places"]:
                inside = all(p == q or p in self.gaz.ancestors(q) for q in places)
                if (places and inside) or (not places and self._home_fallback(feed, topics)):
                    places[p] = max(places.get(p, 0), 3.0)
        elif feed["place_mode"] == "inherit" and not places and feed["source_scope"] == "national" and feed["source_country"] != "XX" and self._home_fallback(feed, topics):
            places[feed["source_country"]] = 1.0
        return topics, self.gaz.with_ancestors(places)

    MAINT_VERSION = "2026-11-03a"    # v0.9.4: clean stored leads, drop languages not collected, fold reprints

    def clean_stored_leads(self, days: int = 14) -> int:
        """Once: the lead cleaning of new items (textutil.clean_leads) applied to the articles already stored, feed by feed."""
        n = 0
        for fid in self.repo.article_feeds(days):
            rows = self.repo.feed_article_leads(fid, days)
            items = [{"title": r["title"], "lead": r["lead"] or ""} for r in rows]
            clean_leads(items)
            ch = [(it["lead"], r["id"], r["seen_day"]) for it, r in zip(items, rows) if it["lead"] != (r["lead"] or "")]
            self.repo.leads_set(ch)
            n += len(ch)
        return n

    async def maintenance_once(self) -> dict | None:
        """After the retag (which recomputed fingerprints and languages from the cleaned leads): delete what is not collected and fold
        reprints. Once per MAINT_VERSION."""
        if (await asyncio.to_thread(self.repo.kv_get, "maint_version")) == self.MAINT_VERSION:
            return None
        res: dict = {}
        if self.settings.get("storage.purge_disabled_languages") and not self.settings.get("sources.all_languages"):
            res["languages"] = await asyncio.to_thread(self.repo.purge_languages, list(self.settings.get("sources.languages")))
        if not self.settings.get("storage.store_reprints"):
            res["reprints"] = await asyncio.to_thread(self.repo.fold_reprints)
        await asyncio.to_thread(self.repo.kv_set, "maint_version", self.MAINT_VERSION)
        log.warning("Maintenance: %s", res)
        return res

    async def retag(self, days: int = 14, batch: int = 2000) -> dict:
        """Tag stored articles again with the current classifier and gazetteer and rebuild the stories' tags. Runs once per TAG_VERSION.
        The routine-item flag of stories is set again once per ROUTINE_VERSION (cheap: headlines only)."""
        if (await asyncio.to_thread(self.repo.kv_get, "maint_version")) != self.MAINT_VERSION \
                and (await asyncio.to_thread(self.repo.kv_get, "leads_cleaned")) != self.MAINT_VERSION:
            n = await asyncio.to_thread(self.clean_stored_leads, days)
            await asyncio.to_thread(self.repo.kv_set, "leads_cleaned", self.MAINT_VERSION)
            await asyncio.to_thread(self.repo.kv_set, "tag_version", None)          # fingerprints and languages from the cleaned leads
            log.warning("Cleaned the leads of %d stored articles", n)
        if (await asyncio.to_thread(self.repo.kv_get, "threads_version")) != self.THREADS_VERSION:
            res = await asyncio.to_thread(self.repo.split_developments, int(self.settings.get("feed.development_hours")), title_key)
            await asyncio.to_thread(self.repo.kv_set, "threads_version", self.THREADS_VERSION)
            await asyncio.to_thread(self.repo.kv_set, "tag_version", None)              # tags and counts of the new developments
            await asyncio.to_thread(self.repo.kv_set, "recluster_due", True)            # merge within developments, join threads
            log.warning("Split long stories into developments: %s", res)
        n = await asyncio.to_thread(self.repo.stories_restore_orphans, title_key)
        if n:
            log.warning("Re-created %d stories whose articles were kept but whose story had been removed", n)
            await asyncio.to_thread(self.repo.kv_set, "tag_version", None)          # their tags and counts are rebuilt below
            await asyncio.to_thread(self.repo.kv_set, "routine_version", None)
        if (await asyncio.to_thread(self.repo.kv_get, "routine_version")) != self.ROUTINE_VERSION:
            n = await asyncio.to_thread(self.repo.stories_mark_routine, days, routine_item)
            await asyncio.to_thread(self.repo.kv_set, "routine_version", self.ROUTINE_VERSION)
            log.info("Marked %d recent stories as routine items", n)
        if (await asyncio.to_thread(self.repo.kv_get, "tag_version")) == self.TAG_VERSION:
            return {"skipped": True}
        last, done, stories = 0, 0, 0
        while True:
            rows = await asyncio.to_thread(self.repo.articles_for_retag, last, batch, days)
            if not rows:
                break
            last = rows[-1]["id"]
            out = []
            for r in rows:
                topics, places = self.tag(r, r["title"], r["lead"])
                lang = detect_lang(f"{r['title']} {r['lead'][:200]}", r.get("source_language") or "")
                out.append((sorted(topics), sorted(places), fingerprint(r["title"], r["lead"]), lang, r["id"], r["seen_day"]))
            async with self._db_gate:                     # one writer of story tags at a time: the refresh and the retag deadlocked otherwise
                stories += await asyncio.to_thread(self.repo.articles_retagged, out)
            done += len(rows)
            await asyncio.sleep(0)
        await asyncio.to_thread(self.repo.kv_set, "tag_version", self.TAG_VERSION)
        await asyncio.to_thread(self.repo.recompute_coverage, days)        # fingerprints changed: independent outlets and coverage too
        log.info("Retagged %d articles, %d stories", done, stories)
        return {"articles": done, "stories": stories}

    # ---- one feed's items ------------------------------------------------------------------------
    def process_items(self, feed: dict, items: list[dict]) -> tuple[int, int]:
        now = dt.datetime.now(UTC)
        max_age = dt.timedelta(hours=int(self.settings.get("refresh.max_item_age_hours")))
        since = (now - dt.timedelta(days=int(self.settings.get("storage.retention_days")))).date()
        fresh = []
        for it in items:
            pub = it["published"] or now
            if pub > now + dt.timedelta(hours=2):
                pub = now
            if now - pub > max_age:
                continue
            it["published"] = pub
            it["hash"] = hashlib.sha1(canonical_url(it["url"]).encode()).hexdigest()
            fresh.append(it)
        if not fresh:
            return 0, 0
        known = self.repo.existing_hashes([i["hash"] for i in fresh], since)
        fresh = [i for i in fresh if i["hash"] not in known]
        seen = set()
        fresh = [i for i in fresh if not (i["hash"] in seen or seen.add(i["hash"]))]
        if not fresh:
            return 0, 0
        fresh = [i for i in fresh if not is_media_url(i["url"])]                # a feed item that is a picture or a video file, not an article
        clean_leads(fresh)                                                       # boilerplate shared by the feed's items, text in another script
        links = {}
        for i in fresh:
            if i.get("link"):
                i["link_hash"] = hashlib.sha1(canonical_url(i["link"]).encode()).hexdigest()
                links[i["link_hash"]] = i
        if links:
            # a post that only shares an article already stored is the same article again
            have = self.repo.existing_hashes(list(links), since)
            fresh = [i for i in fresh if i.get("link_hash") not in have]
        if not fresh:
            return 0, 0
        for d in (now.date(), now.date() + dt.timedelta(days=1)):
            if d not in self._partitions_ready:
                self.repo.ensure_partition(d)
                self._partitions_ready.add(d)
        langs = None if self.settings.get("sources.all_languages") else set(self.settings.get("sources.languages"))
        max_span = dt.timedelta(hours=int(self.settings.get("feed.development_hours")))         # a story takes reports for this long after its first...
        max_gap = dt.timedelta(hours=int(self.settings.get("feed.story_gap_hours")))          # ...unless its reports pause for this long
        for i in fresh:
            i["published"] = effective_time(i["published"], now)
        brk_on = self.settings.get("breaking.enabled")
        brk_n, brk_m, brk_h = (self.settings.get("breaking.outlets"), self.settings.get("breaking.minutes"), self.settings.get("breaking.mark_hours"))
        fold_reprints = not self.settings.get("storage.store_reprints")
        frequent = self.frequent_words()
        new_articles = new_stories = 0
        touched: list[tuple[int, int]] = []
        cites: Counter = Counter()
        with self.repo.pool.connection() as conn:
            for it in sorted(fresh, key=lambda i: i["published"]):
                lang = detect_lang(f"{it['title']} {it['lead'][:200]}", feed.get("source_language") or "")
                if langs is not None and lang not in langs:
                    continue                                                    # a language you do not collect: never stored
                tokens = title_key(it["title"])
                fp = fingerprint(it["title"], it["lead"])
                floor = (now - dt.timedelta(hours=48)).date()
                hit = self.repo.story_by_fingerprint(conn, fp, floor) if fp else None
                sid, thread = (hit["story_id"] if hit else None), None
                twin_fp = fp if sid is not None else None                       # the same text is already stored: this is a reprint, however long after (re-posts and spam of one text fold into the story)
                if sid is None:
                    rare = [t for t in tokens if t not in frequent]
                    cands = self.repo.story_candidates(conn, tokens, now - dt.timedelta(hours=72), rare=rare) if len(tokens) >= 2 else []
                    # a source's own other items are not the same event because their wording matches: templated headlines
                    # ("X open season at home against Y", "Fund buys N shares of Z") would otherwise pile into one story
                    cands = [c for c in cands if not (c["lead_source"] == feed["source_key"] and c["outlets"] <= 1)]
                    mine = anchors(it["title"], self.gaz)
                    veto = lambda c: different_events(mine, self.names_of(c["id"], c["title"]), tokens, c["tokens"], frequent)      # noqa: E731
                    # A story that has closed for this report (its pause or its length limit) does not take it. The report goes to the open story
                    # of its event, the one begun by the first report after the closing; only when there is none does it begin one (in the
                    # closed story's thread). Matching the closed story first made every report after the limit a story of its own, because
                    # the closed story, with the most words, is always the best match.
                    open_c = [c for c in cands if not starts_new_development(it["published"], c["first_seen"], c["last_update"], max_gap, max_span)]
                    sid = match_story(tokens, open_c, veto=veto)
                    if sid is None:
                        closed = match_story(tokens, [c for c in cands if c not in open_c], veto=veto)
                        if closed is not None:
                            thread = next(x for x in cands if x["id"] == closed)["thread_id"]       # same storyline, later event: a new development in its thread
                    elif fold_reprints:
                        twin_fp = self.repo.story_title_twin(conn, sid, norm_title(it["title"]), floor)   # word-for-word headline: wire copy
                if sid is not None and twin_fp is not None and fold_reprints:
                    # Reprints and republished copies are not stored again: only who carried it, when, and the link (for the counts and
                    # the list of outlets), about 150 bytes instead of an article.
                    story_id, outlets = self.repo.ingest_reprint(conn, {"url_hash": it["hash"], "story_id": sid, "source_key": feed["source_key"], "fp": twin_fp,
                                                                         "url": it["url"], "published_at": min(it["published"], now), "seen_day": now.date()})
                    if story_id is None:
                        continue
                    if brk_on and outlets >= brk_n:
                        touched.append((story_id, outlets))
                    continue
                topics, place_codes = self.tag(feed, it["title"], it["lead"])
                a = {"seen_day": now.date(), "source_key": feed["source_key"], "feed_id": feed["id"], "url": it["url"], "url_hash": it["hash"],
                     "title": it["title"], "lead": it["lead"], "published_at": it["published"], "seen_at": now,
                     "country": feed["source_country"], "stype": feed["source_type"], "fp": fp, "lang": lang}
                new = {"title": it["title"], "lead": it["lead"], "tokens": tokens, "lang": lang, "routine": routine_item(it["title"]), "thread": thread}
                story_id, outlets = self.repo.ingest_article(conn, a, sid, new, topics, {p: 1.0 for p in place_codes})
                if it.get("links"):
                    cites.update(cited_domains(it["links"], feed.get("source_domain") or ""))
                new_articles += 1
                if sid is None:
                    new_stories += 1
                elif brk_on and outlets >= brk_n:
                    touched.append((story_id, outlets))
        if cites:
            try:
                self.repo.source_links_add(feed["source_key"], cites, "link")
            except Exception:                                    # noqa: BLE001 - trust evidence; never fails the refresh
                log.exception("Could not record cited sites")
        for story_id, outlets in set(touched):
            if self.repo.mark_breaking(story_id, brk_n, brk_m, brk_h):
                self.bus.publish({"type": "breaking", "story_id": story_id})
        if new_articles:
            self.bus.publish({"type": "stories", "feed": feed["source_key"], "articles": new_articles})
        return new_articles, new_stories
