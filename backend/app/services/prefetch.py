"""Have things ready before they are asked for. After news arrives, for the scopes you look at (Global, your favourites, the places
and topics you opened recently), in that order:

1. the article text of each top story's lead article (free: only the publisher's page is fetched), so the reading pane opens at once;
2. the AI briefing of the scope;
3. the AI digest of each top story reported by two or more sources (an article reading for single-source stories).

AI work stops at the first provider problem and never uses more than a set share of the daily call cap, so what you open yourself
always has room. Anything already prepared for the same material is reused, not made again."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.ai.router import AiUnavailable
from app.ai.service import AiNotReady

log = logging.getLogger(__name__)
UTC = dt.timezone.utc


def _lead(detail: dict, arts: list[dict]) -> dict:
    """The story's lead article (the one its headline comes from); coverage is listed newest first, so it is not simply the first."""
    return next((a for a in arts if a["url"] == detail.get("url")), arts[-1])


class PrefetchService:
    def __init__(self, repo, settings, stories, ai, extract, router):
        self.repo, self.settings, self.stories, self.ai, self.extract, self.router = repo, settings, stories, ai, extract, router
        self.running = False

    def g(self, k: str):
        return self.settings.get(k)

    # ---- which scopes ------------------------------------------------------------------------------
    def note_view(self, place: str, topic: str) -> None:
        """Remember a scope the owner opened (the last few are prepared ahead)."""
        if not place:
            return
        recent = [s for s in (self.repo.kv_get("recent_scopes") or []) if (s["place"], s["topic"]) != (place, topic)]
        recent.insert(0, {"place": place, "topic": topic, "at": dt.datetime.now(UTC).isoformat()})
        self.repo.kv_set("recent_scopes", recent[:12])

    def scopes(self) -> list[tuple[str, str]]:
        out = [("world", "")]
        for f in self.repo.favourites():
            out.append((f["place"], f["topic"]))
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=3)).isoformat()
        for s in self.repo.kv_get("recent_scopes") or []:
            if s["at"] > cutoff:
                out.append((s["place"], s["topic"]))
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq[: int(self.g("ai.prefetch_scopes"))]

    # ---- when ----------------------------------------------------------------------------------------
    def due(self) -> bool:
        if self.running or not self.g("ai.prefetch"):
            return False
        last = self.repo.kv_get("prefetch_last") or {}
        at = last.get("at")
        if at and dt.datetime.now(UTC) - dt.datetime.fromisoformat(at) < dt.timedelta(minutes=int(self.g("ai.prefetch_minutes"))):
            return False
        return bool(self.repo.one("SELECT 1 AS x FROM stories WHERE last_update > now() - interval '1 hour' LIMIT 1"))

    def _ai_room(self) -> int:
        cap = int(self.g("ai.daily_call_cap"))
        share = int(self.g("ai.prefetch_share")) / 100
        used = self.repo.ai_usage_summary()["calls_today"]
        return max(0, int(cap * share) - used)

    def _ai_ready(self) -> bool:
        return bool(self.g("ai.enabled")) and self.router.any_usable()

    # ---- run -----------------------------------------------------------------------------------------
    async def run(self) -> dict:
        if self.running:
            return {"skipped": "already running"}
        self.running = True
        out = {"scopes": 0, "texts": 0, "briefings": 0, "digests": 0, "readings": 0, "reused": 0, "stopped": ""}
        try:
            scopes = await asyncio.to_thread(self.scopes)
            n = int(self.g("ai.prefetch_stories"))
            ai_on = await asyncio.to_thread(self._ai_ready)
            for place, topic in scopes:
                try:
                    listing = await asyncio.to_thread(self.stories.list, place, topic, "top", n)
                except ValueError:
                    continue
                out["scopes"] += 1
                rows = listing["stories"]
                if self.g("extract.enabled") and self.g("extract.prefetch"):
                    out["texts"] += await self._texts(rows)
                if not ai_on or out["stopped"]:
                    continue
                for job in [("briefing", place, topic)] + [("story" if r["indep"] >= 2 or r["articles"] >= 2 else "article", r, None) for r in rows]:
                    if await asyncio.to_thread(self._ai_room) <= 0:
                        out["stopped"] = "reached the share of today's AI calls set for preparing ahead"
                        break
                    try:
                        res = await self._ai(job)
                    except (AiNotReady, AiUnavailable) as e:
                        out["stopped"] = str(e)[:160]
                        break
                    except LookupError:
                        continue
                    except Exception as e:                       # noqa: BLE001 - one failed item must not stop the rest
                        log.info("Prefetch item failed: %s", e)
                        continue
                    if res is None:
                        continue
                    kind, cached = res
                    out["reused" if cached else {"briefing": "briefings", "story": "digests", "article": "readings"}[kind]] += 1
                if out["stopped"]:
                    ai_on = False
            return out
        finally:
            self.running = False
            await asyncio.to_thread(self.repo.kv_set, "prefetch_last", {"at": dt.datetime.now(UTC).isoformat(), **out})

    async def _ai(self, job) -> tuple[str, bool] | None:
        kind = job[0]
        if kind == "briefing":
            r = await self.ai.briefing(job[1], job[2])
        elif kind == "story":
            r = await self.ai.story(job[1]["id"])
        else:
            d = await asyncio.to_thread(self.stories.detail, job[1]["id"]) or {}
            arts = d.get("articles_list") or []
            if not arts:
                return None
            r = await self.ai.article(_lead(d, arts)["id"])
        return kind, bool((r.get("meta") or {}).get("cached"))

    async def _texts(self, rows: list[dict]) -> int:
        """Fetch and keep the lead article's text of each story (the extractor caches it), a few at a time."""
        sem = asyncio.Semaphore(3)
        done = 0

        async def one(r: dict) -> None:
            nonlocal done
            async with sem:
                try:
                    d = await asyncio.to_thread(self.stories.detail, r["id"])
                    arts = (d or {}).get("articles_list") or []
                    if not arts:
                        return
                    row = await asyncio.to_thread(self.repo.article_get, _lead(d, arts)["id"])
                    t = await asyncio.wait_for(self.extract.read(row), timeout=45)
                    done += 1 if t and t.get("status") != "summary" else 0
                except Exception as e:                           # noqa: BLE001
                    log.debug("Text prefetch skipped: %s", e)

        await asyncio.gather(*(one(r) for r in rows))
        return done

    def status(self) -> dict:
        return {"enabled": bool(self.g("ai.prefetch")), "running": self.running, "last": self.repo.kv_get("prefetch_last"), "scopes": self.scopes(),
                "ai_ready": self._ai_ready(), "ai_room_today": self._ai_room()}
