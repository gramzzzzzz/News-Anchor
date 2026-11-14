"""AI readings and briefings: builds the request from the collected material, calls the router, validates the answer against the
material, caches it. Three tasks: an article reading, a story digest, a scope briefing."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging

from app.ai import prompts as P
from app.ai import validate as V
from app.ai.router import AiRouter, AiUnavailable, extract_json

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
BAND_WORD = {"highly_reliable": "highly reliable", "reliable": "reliable", "mixed": "mixed", "low": "low", "unrated": "not rated", "blocked": "blocked"}
TYPE_WORD = {"news": "news outlet", "public_broadcaster": "public broadcaster", "state_funded_broadcaster": "state-funded broadcaster", "official": "official body",
             "company": "company publication", "specialist": "specialist outlet", "factcheck": "fact-checker", "community": "community link site"}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


class AiNotReady(Exception):
    pass


class AiService:
    def __init__(self, repo, settings, router: AiRouter, extractor, stories_svc):
        self.repo, self.settings, self.router, self.extract, self.stories = repo, settings, router, extractor, stories_svc

    # ---- helpers -----------------------------------------------------------------------------------
    def _prompt(self, kind: str) -> tuple[str, str]:
        s = self.settings.get
        task = s(f"ai.prompt_{kind}")
        return s("ai.prompt_shared"), task

    def _version(self, kind: str) -> str:
        shared, task = self._prompt(kind)
        return hashlib.sha1((shared + task + str(self.settings.get("ai.briefing_length"))).encode()).hexdigest()[:10]

    def _messages(self, kind: str, schema: str, material: str, extra: str = "") -> list[dict]:
        shared, task = self._prompt(kind)
        system = f"{shared}\n\n{task}{extra}\n\nReturn only JSON in exactly this shape (keys and nesting; replace the example values):\n{schema}"
        return [{"role": "system", "content": system}, {"role": "user", "content": material}]

    def _check_ready(self) -> None:
        if not self.settings.get("ai.enabled"):
            raise AiNotReady("AI readings are switched off in Settings → AI.")
        if not self.router.any_usable():
            raise AiNotReady("No AI provider is set up. Add a free key in Settings → AI.")

    async def _cached(self, key: str, refresh: bool) -> dict | None:
        if refresh:
            return None
        row = await asyncio.to_thread(self.repo.ai_cache_get, key, int(self.settings.get("ai.cache_hours")))
        if not row:
            return None
        return {**row["output"], "meta": {**row["output"].get("meta", {}), "cached": True, "created_at": row["created_at"].isoformat(), "provider": row["provider"], "model": row["model"]}}

    async def _store(self, key: str, task: str, version: str, out: dict, provider: str, model: str) -> dict:
        out["meta"] = {**out.get("meta", {}), "cached": False, "provider": provider, "model": model, "created_at": dt.datetime.now(UTC).isoformat()}
        await asyncio.to_thread(self.repo.ai_cache_put, key, task, provider, model, version, out)
        return out

    # ---- article -----------------------------------------------------------------------------------
    async def article(self, article_id: int, refresh: bool = False) -> dict:
        self._check_ready()
        a = await asyncio.to_thread(self.repo.article_get, article_id)
        if not a:
            raise LookupError("Article not found")
        ver = self._version("article")
        key = f"article:{a['url_hash']}:{ver}"
        if (c := await self._cached(key, refresh)):
            return c
        text = await self.extract.read(a, force=False)
        paras = text["paragraphs"] or [a["title"]]
        status = {"full": "full text", "partial": "the opening of the article only (the rest is not available)", "summary": "only the publisher's summary (no article text could be read)"}[text["status"]]
        if text["archive_url"]:
            status += "; taken from an Internet Archive copy"
        material = (f"Source: {a['source_name']} ({TYPE_WORD.get(a['source_type'], a['source_type'])}, {a['source_country']}"
                    f"{', owner: ' + a['source_owner'] if a['source_owner'] else ''})\nPublished: {a['published_at']:%Y-%m-%d %H:%M} UTC\nTitle: {a['title']}\n"
                    f"Text status: {status}\n\nParagraphs:\n" + "\n".join(f"p{i}: {p}" for i, p in enumerate(paras[:60], 1)))
        shown = paras[:60]

        def check(raw: str) -> dict:
            obj, rep = V.validate_article(extract_json(raw), shown)
            obj["quality"] = rep
            return obj

        obj, reply, pid = await self.router.complete("article", self._messages("article", P.ARTICLE_SCHEMA, material), check)
        obj["paragraphs"] = shown
        obj["article"] = {"id": a["id"], "title": a["title"], "url": a["url"], "source": a["source_name"], "source_type": a["source_type"], "band": a["source_band"]}
        obj["meta"] = {"basis": text["status"], "text_method": text["method"], "note": text["note"], "archive_url": text["archive_url"]}
        return await self._store(key, "article", ver, obj, pid, reply.model)

    # ---- story -------------------------------------------------------------------------------------
    async def story(self, story_id: int, full_text: bool = False, refresh: bool = False) -> dict:
        self._check_ready()
        detail = await asyncio.to_thread(self.stories.detail, story_id)
        if not detail:
            raise LookupError("Story not found")
        arts = detail["articles_list"][:12]
        if not arts:
            raise AiNotReady("This story has no articles from the sources you show.")
        ver = self._version("story")
        sig = hashlib.sha1(json.dumps([a["id"] for a in arts] + [full_text]).encode()).hexdigest()[:10]
        key = f"story:{story_id}:{sig}:{ver}"
        if (c := await self._cached(key, refresh)):
            return c
        bodies: dict[int, str] = {}
        if full_text and self.settings.get("extract.enabled"):
            # the three best-rated reports with readable text: try up to six, in rating order, until three have given their text
            order = sorted(arts, key=lambda a: {"highly_reliable": 0, "reliable": 1}.get(a["source"]["band"], 2))[:6]

            async def grab(a: dict) -> None:
                try:
                    row = await asyncio.to_thread(self.repo.article_get, a["id"])
                    t = await asyncio.wait_for(self.extract.read(row), timeout=60)
                    if t["status"] != "summary":
                        bodies[a["id"]] = " ".join(t["paragraphs"])[:3500]
                except Exception as e:                                  # noqa: BLE001 - a failed fetch only means less material
                    log.info("Full text for story digest skipped: %s", e)

            await asyncio.gather(*(grab(a) for a in order[:3]))
            for a in order[3:]:
                if len(bodies) >= 3:
                    break
                await grab(a)
        texts: dict[str, str] = {}
        lines = []
        meta_sources = {}
        for i, a in enumerate(arts, 1):
            sid = f"S{i}"
            s = a["source"]
            body = bodies.get(a["id"]) or a["lead"] or ""
            texts[sid] = f"{a['title']} {body}"
            meta_sources[sid] = {"id": sid, "name": s["name"], "type": s["type"], "band": s["band"], "url": a["url"], "title": a["title"], "country": s["country"],
                                 "published_at": a["published_at"].isoformat() if hasattr(a["published_at"], "isoformat") else str(a["published_at"]),
                                 "full_text": a["id"] in bodies}
            lines.append(f"{sid} | {s['name']} | type: {TYPE_WORD.get(s['type'], s['type'])} | country: {s['country']} | reliability: {BAND_WORD.get(s['band'], s['band'])} | "
                          f"published: {a['published_at']:%Y-%m-%d %H:%M} UTC\nTitle: {a['title']}\n{'Text' if a['id'] in bodies else 'Summary'}: {body[:3500] or '(none)'}")
        material = f"Story headline (from the first report): {detail['title']}\nSources ({len(arts)}):\n\n" + "\n\n".join(lines)

        def check(raw: str) -> dict:
            obj, rep = V.validate_story(extract_json(raw), texts)
            obj["quality"] = rep
            return obj

        obj, reply, pid = await self.router.complete("story", self._messages("story", P.STORY_SCHEMA, material), check)
        obj["sources"] = meta_sources
        obj["story"] = {"id": story_id, "title": detail["title"], "verification": detail.get("verification"),
                        "first_seen": _iso(detail.get("first_seen"))}
        obj["meta"] = {"basis": f"full text of {len(bodies)} article(s), headlines and summaries of the rest" if bodies else "headlines and publisher summaries"}
        return await self._store(key, "story", ver, obj, pid, reply.model)

    # ---- scope briefing ------------------------------------------------------------------------------
    async def briefing(self, place: str, topic: str, refresh: bool = False) -> dict:
        self._check_ready()
        place, topic = self.stories.check_scope(place, topic)
        n = int(self.settings.get("ai.briefing_stories"))
        listing = await asyncio.to_thread(self.stories.list, place, topic, "top", n)
        rows = listing["stories"]
        if not rows:
            raise AiNotReady("There are no stories in this scope to brief.")
        length = self.settings.get("ai.briefing_length")
        ver = self._version("scope")
        sig = hashlib.sha1(json.dumps([(r["id"], r["articles"]) for r in rows]).encode()).hexdigest()[:10]
        key = f"scope:{place}:{topic}:{sig}:{ver}"
        if (c := await self._cached(key, refresh)):
            return c
        texts: dict[str, str] = {}
        meta_sources: dict[str, dict] = {}
        blocks, story_ids = [], set()
        counter = 0
        details = await asyncio.gather(*(asyncio.to_thread(self.stories.detail, r["id"]) for r in rows))
        for rank, (r, d) in enumerate(zip(rows, details), 1):
            if not d:
                continue
            story_ids.add(f"D{rank}")
            ls = []
            seen = set()
            for a in d["articles_list"][:5]:
                if a["source"]["key"] in seen:
                    continue
                seen.add(a["source"]["key"])
                counter += 1
                sid = f"S{counter}"
                s = a["source"]
                texts[sid] = f"{a['title']} {a['lead'] or ''}"
                meta_sources[sid] = {"id": sid, "name": s["name"], "type": s["type"], "band": s["band"], "url": a["url"], "title": a["title"], "country": s["country"],
                                     "story_id": r["id"]}
                ls.append(f"  {sid} | {s['name']} | {TYPE_WORD.get(s['type'], s['type'])} | {s['country']} | reliability: {BAND_WORD.get(s['band'], s['band'])}\n  Title: {a['title']}\n  Summary: {(a['lead'] or '(none)')[:400]}")
            blocks.append(f"D{rank} | rank {rank}{' | BREAKING' if r['breaking'] else ''} | {r['outlets']} outlet(s) | first reported {r['first_seen']:%Y-%m-%d %H:%M} UTC\n" + "\n".join(ls))
        scope_name = self.stories.scope_label(place, topic)
        material = f"Scope: {scope_name}\nTime window: last {self.settings.get('feed.window_hours')} hours (now {dt.datetime.now(UTC):%Y-%m-%d %H:%M} UTC)\n\n" + "\n\n".join(blocks)
        extra = f"\nLength: about {P.LENGTH_WORDS[length]} words in total ({length})."

        def check(raw: str) -> dict:
            obj, rep = V.validate_scope(extract_json(raw), texts, story_ids)
            obj["quality"] = rep
            return obj

        obj, reply, pid = await self.router.complete("scope", self._messages("scope", P.SCOPE_SCHEMA, material, extra), check)
        obj["sources"] = meta_sources
        obj["scope"] = {"place": place, "topic": topic, "name": scope_name, "stories": {f"D{i}": {"id": r["id"], "title": r["title"], "breaking": r["breaking"], "verification": r["verification"]} for i, r in enumerate(rows, 1)}}
        obj["meta"] = {"basis": "headlines and publisher summaries of the top stories", "length": length}
        return await self._store(key, "scope", ver, obj, pid, reply.model)

    # ---- background ----------------------------------------------------------------------------------
    async def background(self, limit: int = 5) -> dict:
        """Prepare cited briefings for the owner's favourites, so they open instantly. Only when the owner switched it on;
        stops at the first provider problem rather than burning through free quotas."""
        done, errors = 0, 0
        favs = await asyncio.to_thread(self.repo.favourites)
        for f in favs[:limit]:
            try:
                await self.briefing(f["place"], f["topic"])
                done += 1
            except (AiNotReady, AiUnavailable) as e:
                errors += 1
                log.info("Background briefing stopped: %s", e)
                break
            except Exception:                                            # noqa: BLE001
                log.exception("Background briefing failed")
                errors += 1
        return {"briefings": done, "stopped": errors}
