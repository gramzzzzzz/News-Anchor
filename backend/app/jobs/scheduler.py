"""Background loop inside the app process: refresh feeds when due, enforce storage limits, sample memory."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, ingest, storage, governor, repo, trust=None, settings=None, discovery=None, briefer=None, social=None, prefetch=None):
        self.ingest, self.storage, self.governor, self.repo = ingest, storage, governor, repo
        self.trust, self.settings, self.discovery = trust, settings, discovery
        self.briefer = briefer                 # async callable: prepares briefings for favourites (when the owner has switched that on)
        self._last_brief = 0.0
        self.social, self.prefetch = social, prefetch
        self._last_social = 0.0
        self._official_task = None
        self._prefetch_task = None
        self._stop = asyncio.Event()
        self._last_storage = 0.0
        self._last_intervals = 0.0

    async def _reanalyse_intervals(self) -> None:
        """Once a day: every working feed's check interval is set again from its last week of articles (IngestService.next_interval backs a
        quiet feed off step by step; this undoes it when the feed has become busy, and keeps every feed within two hours)."""
        from app.services.ingest import interval_bounds
        last = await asyncio.to_thread(self.repo.kv_get, "feed_intervals_at")
        now = dt.datetime.now(dt.timezone.utc)
        if last and now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=24):
            return
        lo, hi = interval_bounds(int(self.settings.get("refresh.news_minutes")) * 60) if self.settings else (300, 7200)
        res = await asyncio.to_thread(self.repo.feeds_reanalyse, lo, hi)
        await asyncio.to_thread(self.repo.kv_set, "feed_intervals_at", now.isoformat())
        await asyncio.to_thread(self.repo.kv_set, "feed_intervals_last", res)
        log.info("Feed check intervals set again from the last week's activity: %s", res)

    async def run(self) -> None:
        log.info("Scheduler started")
        await asyncio.to_thread(self.storage.enforce)
        while not self._stop.is_set():
            try:
                self.governor.sample()
                if time.monotonic() - self._last_storage > 600:
                    self._last_storage = time.monotonic()
                    await asyncio.to_thread(self.storage.enforce)
                if self.trust and not self.trust.running and await asyncio.to_thread(self._trust_due):
                    jid = await asyncio.to_thread(self.repo.job_create, "refresh-trust")
                    asyncio.create_task(self.trust.refresh(jid))
                if self.trust and not self.trust.running and await asyncio.to_thread(self._estimate_due):
                    jid = await asyncio.to_thread(self.repo.job_create, "estimate-trust")
                    asyncio.create_task(self.trust.run_estimate(jid))
                if self.trust and self.trust.running is False and await asyncio.to_thread(self._trust_partial_due):
                    jid = await asyncio.to_thread(self.repo.job_create, "refresh-trust")
                    asyncio.create_task(self.trust.refresh(jid, partial=True))
                if self.discovery:
                    for what in await asyncio.to_thread(self.discovery.due_all):
                        if what == "directory":
                            jid = await asyncio.to_thread(self.repo.job_create, "discover-sources")
                            asyncio.create_task(self.discovery.run_directory(jid))
                        elif what == "batch":
                            asyncio.create_task(self.discovery.run_batch())
                        elif what == "web":
                            asyncio.create_task(self.discovery.run_web())
                        elif what == "stream":
                            asyncio.create_task(self.discovery.run_stream())
                        elif what == "editions":
                            asyncio.create_task(self.discovery.run_editions())
                        elif what == "wikidata":
                            asyncio.create_task(self.discovery.run_wikidata())
                        elif what == "rebalance":
                            await self.discovery.run_rebalance()
                        elif what == "lifecycle":
                            await self.discovery.run_lifecycle()
                if self.social and time.monotonic() - self._last_social > 60:
                    self._last_social = time.monotonic()
                    await self.social.sync()                         # settings changes take effect within a minute
                    if (self._official_task is None or self._official_task.done()) and await asyncio.to_thread(self.social.official_due):
                        self._official_task = asyncio.create_task(self.social.refresh_official())
                if self.prefetch and (self._prefetch_task is None or self._prefetch_task.done()) and await asyncio.to_thread(self.prefetch.due):
                    self._prefetch_task = asyncio.create_task(self.prefetch.run())
                if self.briefer and self.settings and self.settings.get("ai.auto_briefing") and time.monotonic() - self._last_brief > 1800:
                    self._last_brief = time.monotonic()
                    asyncio.create_task(self.briefer())
                if time.monotonic() - self._last_intervals > 600:
                    self._last_intervals = time.monotonic()
                    await self._reanalyse_intervals()
                await self.ingest.merge_tick()
                if not self.ingest.running and await asyncio.to_thread(self.repo.feeds_due_count, None if (self.settings and self.settings.get("sources.all_languages")) or not self.settings else list(self.settings.get("sources.languages"))):
                    await self.ingest.run_cycle()
            except Exception:
                log.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    def _trust_partial_due(self) -> bool:
        """Sources found by discovery are rated soon after they arrive, without redoing the whole crawl."""
        if not self.repo.kv_get("trust_dirty", False):
            return False
        attempt = self.repo.kv_get("trust_last_attempt")
        if attempt and dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(attempt) < dt.timedelta(minutes=30):
            return False
        return bool(self.repo.kv_get("trust_last_run"))              # before the first full crawl, the full crawl covers everything

    def _estimate_due(self) -> bool:
        """Daily: who cites whom and pickups change every day; the published estimates are re-read monthly."""
        if not (self.settings and self.settings.get("trust.estimate")) or not self.repo.kv_get("trust_last_run"):
            return False
        now = dt.datetime.now(dt.timezone.utc)
        attempt = self.repo.kv_get("trust_estimate_attempt")
        if attempt and now - dt.datetime.fromisoformat(attempt) < dt.timedelta(hours=1):
            return False
        last = self.repo.kv_get("trust_estimate")
        return not last or now - dt.datetime.fromisoformat(last["at"]) > dt.timedelta(hours=24)

    def _trust_due(self) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        attempt = self.repo.kv_get("trust_last_attempt")
        if attempt and now - dt.datetime.fromisoformat(attempt) < dt.timedelta(hours=1):
            return False                                  # a failed run is retried after an hour, never in a tight loop
        last = self.repo.kv_get("trust_last_run")
        if not last:
            return True
        return now - dt.datetime.fromisoformat(last["at"]) > dt.timedelta(days=int(self.settings.get("trust.refresh_days")))

    def stop(self) -> None:
        self._stop.set()
