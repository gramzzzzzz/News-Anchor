"""HTTP layer: validation, auth, error mapping. No business logic here."""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.services.textutil import title_key
from app.api import auth
from app.config import Config, load_config
from app.container import Container, build
from app.jobs.scheduler import Scheduler
from app.services.settings_service import GROUP_ORDER, PROFILES, SettingError
from app.dao.social import SocialError
from app.services.stories import ScopeError
from app.services.trust_scoring import OVERRIDE_BANDS, SIGNALS

log = logging.getLogger(__name__)


class Password(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class SavedBody(BaseModel):
    saved: bool


class EnabledBody(BaseModel):
    enabled: bool


class BulkBody(BaseModel):
    codes: list[str]
    enabled: bool


class FavBody(BaseModel):
    place: str = "world"
    topic: str = ""


class SettingsBody(BaseModel):
    changes: dict


class ResetBody(BaseModel):
    key: str | None = None
    group: str | None = None


class ProfileBody(BaseModel):
    name: str


class ChangePassword(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=8, max_length=200)


class OverrideBody(BaseModel):
    band: str | None = None


class ExploreBody(BaseModel):
    q: str = ""
    since: str | None = None
    until: str | None = None
    days: int = 7
    place: str = ""
    topic: str = ""
    types: list[str] = []
    bands: list[str] = []
    countries: list[str] = []
    sources: list[str] = []
    limit: int = 25
    offset: int = 0


class CompareBody(ExploreBody):
    queries: list[str] = []


class AiProviderBody(BaseModel):
    enabled: bool | None = None
    key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=300)
    daily_limit: int | None = Field(default=None, ge=0, le=100000)


class AiOrderBody(BaseModel):
    order: list[str]


class AiArticleBody(BaseModel):
    article_id: int
    refresh: bool = False


class AiStoryBody(BaseModel):
    story_id: int
    full_text: bool = False
    refresh: bool = False


class AiBriefingBody(BaseModel):
    place: str = "world"
    topic: str = ""
    refresh: bool = False


REGROUP_VERSION = "2026-11-12-gap-rule-d"


def create_app(config: Config | None = None, container: Container | None = None) -> FastAPI:
    c = container or build(config or load_config())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c.repo.open()
        await asyncio.to_thread(c.repo.jobs_close_interrupted)
        c.bus.bind(asyncio.get_running_loop())
        await asyncio.to_thread(c.ingest.seed)
        await asyncio.to_thread(c.storage.ensure_partitions)
        await c.fetcher.start()
        sched = Scheduler(c.ingest, c.storage, c.governor, c.repo, c.trust, c.settings, c.discovery, c.ai.background, social=c.social,
                          prefetch=getattr(c, "prefetch", None)) if c.config.scheduler else None
        task = asyncio.create_task(sched.run()) if sched else None
        async def startup_jobs():
            await c.ingest.retag()
            async with c.merger._lock, c.ingest._db_gate:   # one writer of stories at a time; always the merge lock first, then the gate (a merge pass takes them in that order)
                await c.ingest.maintenance_once()        # languages not collected, reprints (v0.9.4)
                if c.storylines:
                    await c.storylines.repair_once()
            if await asyncio.to_thread(c.repo.kv_get, "recluster_due"):
                # after the stories were cut into developments: merge within each development, join the threads, recount
                res = await c.merger.run(max_new=40000, full=True)
                await asyncio.to_thread(c.repo.recompute_coverage, 5)
                await asyncio.to_thread(c.repo.kv_set, "recluster_due", False)
                logging.getLogger(__name__).warning("Re-clustered after the thread split: %s", res)
            if await asyncio.to_thread(c.repo.kv_get, "regroup_version") != REGROUP_VERSION:
                # the rule for putting reports in one story changed (a story is open while its reports keep coming, and two established stories no longer
                # merge): stories that grew past the limit by the interim rules are cut into developments again, the date-only times are set right, and the
                # stories of the last hours are compared again
                await asyncio.to_thread(c.repo.kv_set, "regroup_version", REGROUP_VERSION)          # set first: a run that gets killed must not repeat at every start
                fixed = await asyncio.to_thread(c.repo.fix_date_only_times, 3)
                async with c.merger._lock, c.ingest._db_gate:
                    cut = await asyncio.to_thread(c.repo.split_developments, int(c.settings.get("feed.development_hours")), title_key, 6)
                await asyncio.to_thread(c.repo.kv_set, "tag_version", None)                     # tags of the new developments are built again (last 6 days only)
                await c.ingest.retag(days=6)
                await asyncio.to_thread(c.repo.recompute_coverage, 6)
                res = await c.merger.run(full=True, recent_hours=4)
                logging.getLogger(__name__).warning("Grouped again under the new rule: times %s, cut %s, stories %s", fixed, cut, res)

        app.state.retag_task = asyncio.create_task(startup_jobs()) if c.config.scheduler else None   # held so it is not collected
        yield
        if sched:
            sched.stop()
            await task
        await c.embedder.close()
        await c.translator.close()
        await c.ai_router.client.close()
        await c.fetcher.close()
        c.repo.close()

    app = FastAPI(title="News Anchor", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.container = c

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'")
        return resp

    @app.exception_handler(ScopeError)
    async def scope_error(_: Request, e: ScopeError):
        return Response(content=str(e), status_code=404, media_type="text/plain")

    @app.exception_handler(SettingError)
    async def setting_error(_: Request, e: SettingError):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(e)}, status_code=422)

    # ---- public ----------------------------------------------------------------------------------
    pub = APIRouter(prefix="/api")

    @pub.get("/health")
    def health():
        c.repo.one("SELECT 1")
        return {"ok": True}

    @pub.get("/auth/status")
    def auth_status(request: Request):
        return {"configured": bool(c.repo.kv_get("password")), "authenticated": auth.verify_token(c.config.secret_key, request.cookies.get(auth.COOKIE))}

    def _login(resp: Response) -> None:
        resp.set_cookie(auth.COOKIE, auth.make_token(c.config.secret_key, c.config.session_hours), httponly=True, samesite="strict",
                        secure=c.config.cookie_secure, max_age=c.config.session_hours * 3600, path="/")

    @pub.post("/auth/setup")
    def setup(body: Password, request: Request, resp: Response):
        auth.rate_limit(request.client.host if request.client else "?")
        if c.repo.kv_get("password"):
            raise HTTPException(409, "A password is already set")
        c.repo.kv_set("password", auth.hash_password(body.password))
        _login(resp)
        return {"ok": True}

    @pub.post("/auth/login")
    def login(body: Password, request: Request, resp: Response):
        auth.rate_limit(request.client.host if request.client else "?")
        stored = c.repo.kv_get("password")
        if not stored or not auth.check_password(body.password, stored):
            raise HTTPException(401, "Wrong password")
        _login(resp)
        return {"ok": True}

    @pub.post("/auth/logout")
    def logout(resp: Response):
        resp.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @pub.get("/social/x/callback")
    async def x_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        """X sends the browser back here after login. The session cookie is not sent on this cross-site return (it is SameSite=Strict),
        so the one-time state value, created by the logged-in owner and valid for 15 minutes, is what proves the login was started here."""
        from fastapi.responses import RedirectResponse
        from urllib.parse import quote
        if error or not code:
            return RedirectResponse(f"/settings?social=x-error&msg={quote(error or 'cancelled')}#social", status_code=303)
        try:
            who = await c.social.x_callback(code, state)
        except SocialError as e:
            return RedirectResponse(f"/settings?social=x-error&msg={quote(str(e)[:150])}#social", status_code=303)
        return RedirectResponse(f"/settings?social=x-ok&user={quote(who['username'])}#social", status_code=303)

    app.include_router(pub)

    # ---- protected -------------------------------------------------------------------------------
    api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_session)])

    @api.get("/stories")
    def stories(place: str = "world", topic: str = "", tab: str = "top", view: str = "all", limit: int | None = None, offset: int = 0):
        out = c.stories.list(place, topic, tab, limit, offset, view=view)
        if offset == 0 and tab in ("top", "latest") and c.prefetch:
            c.prefetch.note_view(place, topic)                     # scopes you open are prepared ahead next time
        return out

    @api.get("/storylines")
    def storylines(place: str = "world", topic: str = "", hours: int | None = None, limit: int = 20, offset: int = 0):
        return c.storylines.list(place, topic, hours, limit, offset)

    @api.get("/stories/{sid}")
    def story(sid: int):
        d = c.stories.detail(sid)
        if not d:
            raise HTTPException(404, "Story not found (it may be older than the retention window)")
        return d

    @api.put("/stories/{sid}/saved")
    def save(sid: int, body: SavedBody):
        c.repo.saved_set(sid, body.saved)
        c.stories.clear_cache()
        return {"ok": True}

    @api.get("/places/menu")
    def places_menu(place: str = "world", topic: str = ""):
        return c.stories.place_menu(place, topic)

    @api.get("/topics/menu")
    def topics_menu(parent: str = "", place: str = "world"):
        return c.topics.menu(parent, place)

    @api.get("/index")
    def index():
        return c.stories.index()

    @api.get("/topics")
    def topics():
        return {"topics": c.topics.tree()}

    @api.put("/topics/{code}")
    def topic_set(code: str, body: EnabledBody):
        try:
            c.topics.set_enabled(code, body.enabled)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @api.post("/topics/bulk")
    def topics_bulk(body: BulkBody):
        return {"changed": c.topics.set_many(body.codes, body.enabled)}

    @api.post("/topics/reset")
    def topics_reset():
        c.topics.reset()
        return {"ok": True}

    @api.get("/favourites")
    def favourites():
        out = []
        for f in c.repo.favourites():
            try:
                out.append({"place": f["place"], "topic": f["topic"], "label": c.stories.scope_label(*c.stories.check_scope(f["place"], f["topic"]))})
            except ScopeError:
                continue                                  # a place or topic that no longer exists
        return {"favourites": out}

    @api.post("/favourites")
    def fav_add(body: FavBody):
        place, topic = c.stories.check_scope(body.place, body.topic)
        c.repo.favourite_add(place, topic)
        return {"ok": True}

    @api.delete("/favourites")
    def fav_del(place: str = "world", topic: str = ""):
        c.repo.favourite_remove(place, topic)
        return {"ok": True}

    @api.get("/settings")
    def settings_get():
        return {"settings": c.settings.snapshot(), "groups": GROUP_ORDER, "profiles": list(PROFILES)}

    @api.put("/settings")
    def settings_put(body: SettingsBody):
        c.settings.set_many(body.changes)
        _maybe_rescore(body.changes.keys())
        return {"ok": True}

    def _maybe_rescore(keys) -> None:
        if any(k.startswith(("sources.floor", "sources.show_unrated", "sources.unrated_weight", "sources.gamma", "trust.w.", "trust.band_")) for k in keys):
            c.trust.rescore()

    @api.post("/settings/reset")
    def settings_reset(body: ResetBody):
        n = c.settings.reset(body.key, body.group)
        _maybe_rescore([body.key] if body.key else ["sources.floor"])
        return {"reset": n}

    @api.post("/settings/profile")
    def settings_profile(body: ProfileBody):
        c.settings.apply_profile(body.name)
        return {"ok": True}

    @api.get("/sources")
    def sources(q: str = "", status: str = "", band: str = "", country: str = "", type: str = "", language: str = "", origin: str = "", sort: str = "name",
                limit: int = 100, offset: int = 0, facets: bool = False):
        rows, total = c.repo.sources_page(q=q.strip(), status=status, band=band, country=country, typ=type, language=language, origin=origin, sort=sort,
                                          limit=max(1, min(limit, 500)), offset=max(0, offset))
        out = {"sources": rows, "total": total}
        if facets:
            out["facets"] = c.repo.source_facets()
        return out

    @api.get("/sources/{key}")
    def source(key: str):
        s = c.repo.source_get(key)
        if not s:
            raise HTTPException(404, "Unknown source")
        sigs = [{**x, "label": SIGNALS.get(x["signal"], {}).get("label", x["signal"]), "help": SIGNALS.get(x["signal"], {}).get("help", ""),
                 "expert": SIGNALS.get(x["signal"], {}).get("expert", False)} for x in c.repo.signals_for(key)]
        return {"source": s, "feeds": c.repo.source_feeds(key), "signals": sigs, "history": c.repo.source_history(key)}

    @api.put("/sources/{key}/override")
    def source_override(key: str, body: OverrideBody):
        if not c.repo.source_get(key):
            raise HTTPException(404, "Unknown source")
        if body.band is not None and body.band not in OVERRIDE_BANDS:
            raise HTTPException(422, "Band must be one of " + ", ".join(OVERRIDE_BANDS))
        c.repo.source_override_set(key, body.band)
        c.trust.rescore()
        return {"ok": True}

    @api.get("/trust/method")
    def trust_method():
        return {**c.trust.method(), "last_run": c.repo.kv_get("trust_last_run"), "running": c.trust.running}

    @api.post("/jobs/estimate-trust")
    async def estimate_trust():
        """The measured trust evidence and estimates for sources without an expert rating, now (no crawl)."""
        if c.trust.running:
            return {"job_id": None, "running": True}
        jid = await asyncio.to_thread(c.repo.job_create, "estimate-trust")
        asyncio.create_task(c.trust.run_estimate(jid))
        return {"job_id": jid, "running": True}

    @api.post("/jobs/refresh-trust")
    async def refresh_trust():
        if c.trust.running:
            return {"job_id": None, "running": True}
        jid = await asyncio.to_thread(c.repo.job_create, "refresh-trust")
        asyncio.create_task(c.trust.refresh(jid))
        return {"job_id": jid, "running": True}

    @api.get("/status")
    def status():
        return {"counts": c.repo.counts(), "running": c.ingest.running, "paused": bool(c.repo.kv_get("ingest_paused", False)),
                "last_job": c.repo.job_last("refresh-news"), "next_poll": c.repo.next_poll_at(None if c.settings.get("sources.all_languages") else list(c.settings.get("sources.languages")))}

    @api.post("/jobs/refresh-news")
    async def refresh():
        if c.ingest.running:
            return {"job_id": None, "running": True}
        jid = await asyncio.to_thread(c.repo.job_create, "refresh-news")
        await asyncio.to_thread(c.repo.feeds_poll_now)
        asyncio.create_task(c.ingest.run_cycle(jid))
        return {"job_id": jid, "running": True}

    @api.get("/jobs/{jid}")
    def job(jid: int):
        j = c.repo.job_get(jid)
        if not j:
            raise HTTPException(404, "Unknown job")
        return j

    @api.get("/storage")
    def storage():
        return c.storage.status()

    @api.post("/storage/enforce")
    def storage_enforce():
        return {"actions": c.storage.enforce()}

    @api.get("/resources")
    def resources():
        return c.governor.snapshot()

    @api.get("/stats")
    def stats():
        return c.stats.snapshot()

    @api.get("/audit")
    def audit():
        return {"entries": c.repo.audit_recent()}

    # ---- discovery -------------------------------------------------------------------------------
    @api.get("/discovery")
    def discovery_status():
        return c.discovery.status()

    @api.post("/jobs/discover-sources")
    async def discover_now():
        if c.discovery.dir_running:
            return {"job_id": None, "running": True}
        jid = await asyncio.to_thread(c.repo.job_create, "discover-sources")
        asyncio.create_task(c.discovery.run_directory(jid))
        return {"job_id": jid, "running": True}

    @api.post("/auth/password")
    def change_password(body: ChangePassword, request: Request):
        auth.rate_limit(request.client.host if request.client else "?")
        stored = c.repo.kv_get("password")
        if not stored or not auth.check_password(body.current, stored):
            raise HTTPException(403, "The current password is wrong")
        c.repo.kv_set("password", auth.hash_password(body.new))
        c.repo.audit("auth.password", "changed")
        return {"ok": True}

    @api.post("/jobs/discover-web")
    async def discover_web_now():
        if c.discovery.web_running:
            return {"job_id": None, "running": True}
        jid = await asyncio.to_thread(c.repo.job_create, "discover-web")
        asyncio.create_task(c.discovery.run_web(job_id=jid))
        return {"job_id": jid, "running": True}

    # ---- social --------------------------------------------------------------------------------------
    @api.get("/social")
    def social_status():
        return c.social.status()

    @api.post("/social/sync")
    async def social_sync():
        return await c.social.sync(force=True)

    @api.post("/social/official")
    async def social_official():
        asyncio.create_task(c.social.refresh_official())
        return {"running": True}

    @api.get("/social/x/login")
    def x_login(request: Request):
        """Start the X login: the browser goes to X, which sends it back to /api/social/x/callback on this server."""
        from fastapi.responses import RedirectResponse
        try:
            return RedirectResponse(c.social.x_login(str(request.base_url).rstrip("/") + "/api/social/x/callback"), status_code=303)
        except SocialError as e:
            raise HTTPException(409, str(e))

    @api.post("/social/x/logout")
    def x_logout():
        c.social.x_logout()
        return {"ok": True}

    @api.post("/jobs/discover/{what}")
    async def discover_part(what: str):
        """Start one discovery finder now: stream (GDELT), editions (Google News), wikidata (per country) or rebalance."""
        runs = {"stream": ("stream_running", c.discovery.run_stream), "editions": ("editions_running", c.discovery.run_editions),
                "wikidata": ("wd_running", c.discovery.run_wikidata)}
        if what == "rebalance":
            return await c.discovery.run_rebalance()
        if what not in runs:
            raise HTTPException(404, "Unknown discovery step")
        flag, fn = runs[what]
        if getattr(c.discovery, flag):
            return {"job_id": None, "running": True}
        jid = await asyncio.to_thread(c.repo.job_create, f"discover-{what}")
        asyncio.create_task(fn(jid))
        return {"job_id": jid, "running": True}

    @api.post("/jobs/recluster")
    async def recluster():
        """Compare every story of the last 48 hours with every other once (after a change to the merging rule), not only the new ones."""
        jid = await asyncio.to_thread(c.repo.job_create, "recluster")

        async def go():
            try:
                res = await c.merger.run(max_new=20000, full=True)
                await asyncio.to_thread(c.repo.recompute_coverage, 3)
                await asyncio.to_thread(c.repo.job_update, jid, state="done", result=res)
            except Exception as e:                                      # noqa: BLE001
                await asyncio.to_thread(c.repo.job_update, jid, state="failed", result={"error": str(e)[:200]})
        asyncio.create_task(go())
        return {"job_id": jid, "running": True}

    @api.post("/jobs/repair-storylines")
    async def repair_storylines():
        """Take reports out of stories, and developments out of storylines, when they name different people, teams or places."""
        jid = await asyncio.to_thread(c.repo.job_create, "repair-storylines")

        async def go():
            try:
                async with c.merger._lock, c.ingest._db_gate:
                    res = await asyncio.to_thread(c.storylines.repair)
                await asyncio.to_thread(c.repo.job_update, jid, state="done", result=res)
            except Exception as e:                                      # noqa: BLE001
                await asyncio.to_thread(c.repo.job_update, jid, state="failed", result={"error": str(e)[:200]})
        asyncio.create_task(go())
        return {"job_id": jid, "running": True}

    @api.post("/jobs/find-feeds")
    async def find_feeds_now():
        if c.discovery.batch_running:
            return {"running": True}
        asyncio.create_task(c.discovery.run_batch())
        return {"running": True}

    @api.post("/sources/no-feed/requeue")
    def no_feed_requeue():
        n = c.repo.no_feed_requeue()
        c.repo.audit("sources.no_feed_requeue", f"{n} sources")
        return {"requeued": n}

    @api.post("/sources/no-feed/clear")
    def no_feed_clear():
        n = c.repo.no_feed_clear()
        c.repo.audit("sources.no_feed_clear", f"{n} sources")
        return {"cleared": n}

    @api.post("/sources/{key}/recheck")
    def source_recheck(key: str):
        if not c.repo.source_recheck(key):
            raise HTTPException(409, "Only discovered sources can be re-checked")
        return {"ok": True}

    # ---- search, explore, text ---------------------------------------------------------------------
    @api.get("/search")
    async def search(q: str, place: str = "", topic: str = "", days: int = 30, limit: int = 30):
        try:
            return await c.search.search(q, place=place, topic=topic, days=days, limit=max(1, min(limit, 100)))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @api.post("/explore")
    async def explore(body: ExploreBody):
        try:
            return await c.search.explore(body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))

    @api.post("/explore/compare")
    async def explore_compare(body: CompareBody):
        try:
            return await c.search.compare(body.queries, body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))

    @api.get("/translate/status")
    def translate_status():
        return c.translator.status()

    @api.get("/search/status")
    def search_status():
        be = c.repo.kv_get("embed_backend")
        e = c.repo.embedded_count(be)
        return {**e, "backend": be, "mode": c.embedder.mode(), "model_loaded": c.embedder.loaded, "model_error": c.embedder.last_error}

    @api.get("/articles/{aid}/text")
    async def article_text(aid: int, refresh: bool = False):
        a = await asyncio.to_thread(c.repo.article_get, aid)
        if not a:
            raise HTTPException(404, "Article not found (it may be older than the retention window)")
        t = await c.extract.read(a, force=refresh)
        return {**t, "article": {"id": a["id"], "title": a["title"], "url": a["url"], "published_at": a["published_at"], "source_key": a["source_key"],
                                 "source_name": a["source_name"], "source_type": a["source_type"], "band": a["source_band"], "story_id": a["story_id"]}}

    @api.get("/mediacloud/context")
    async def mediacloud_context(q: str, days: int = 30, collection_id: int | None = None):
        from app.services.mediacloud_context import context
        try:
            return await context(c, q, days, collection_id)
        except ValueError as e:
            raise HTTPException(422, str(e))

    # ---- AI -----------------------------------------------------------------------------------------
    from app.ai.router import AiUnavailable
    from app.ai.service import AiNotReady

    def _ai_errors(fn):
        @functools.wraps(fn)
        async def run(*a, **k):
            try:
                return await fn(*a, **k)
            except AiNotReady as e:
                raise HTTPException(409, str(e))
            except AiUnavailable as e:
                raise HTTPException(503, str(e))
            except LookupError as e:
                raise HTTPException(404, str(e))
        return run

    @api.get("/ai/status")
    def ai_status():
        return {"providers": c.ai_router.public_state(), "usage": c.repo.ai_usage_summary(), "ready": c.ai_router.any_usable() and bool(c.settings.get("ai.enabled")),
                "enabled": bool(c.settings.get("ai.enabled")), "daily_cap": c.settings.get("ai.daily_call_cap")}

    @api.put("/ai/providers/{pid}")
    def ai_provider_set(pid: str, body: AiProviderBody):
        try:
            c.ai_router.set_provider(pid, body.model_dump(exclude_none=True))
        except ValueError as e:
            raise HTTPException(422, str(e))
        c.repo.audit("ai.provider", pid)
        return {"ok": True}

    @api.delete("/ai/providers/{pid}")
    def ai_provider_reset(pid: str):
        c.ai_router.reset_provider(pid)
        c.repo.audit("ai.provider.reset", pid)
        return {"ok": True}

    @api.put("/ai/order")
    def ai_order(body: AiOrderBody):
        c.ai_router.set_order(body.order)
        return {"ok": True}

    @api.post("/ai/providers/{pid}/test")
    async def ai_provider_test(pid: str):
        from app.ai.providers import BY_ID
        if pid not in BY_ID:
            raise HTTPException(404, "Unknown provider")
        return await c.ai_router.test(pid)

    @api.post("/ai/article")
    @_ai_errors
    async def ai_article(body: AiArticleBody):
        return await c.ai.article(body.article_id, body.refresh)

    @api.get("/ai/ready")
    def ai_ready(story_id: int | None = None, place: str = "", topic: str = ""):
        """What is already prepared, without calling any AI: the newest digest of a story or briefing of a scope.
        'current' is false when the story or scope has new material since it was made (open it to make a fresh one)."""
        hours = int(c.settings.get("ai.cache_hours"))
        if story_id is not None:
            row = c.repo.ai_cache_latest(f"story:{story_id}:", hours)
            if not row:
                return {"ready": False}
            ids = [a["id"] for a in ((c.stories.detail(story_id) or {}).get("articles_list") or [])[:12]]
            current = any(row["key"].startswith(f"story:{story_id}:{hashlib.sha1(json.dumps(ids + [ft]).encode()).hexdigest()[:10]}:") for ft in (False, True))
        else:
            p, t = c.stories.check_scope(place, topic)
            row = c.repo.ai_cache_latest(f"scope:{p}:{t}:", hours)
            if not row:
                return {"ready": False}
            current = None
        out = row["output"]
        return {"ready": True, "current": current, "result": {**out, "meta": {**out.get("meta", {}), "cached": True, "created_at": row["created_at"].isoformat(),
                                                                              "provider": row["provider"], "model": row["model"]}}}

    @api.get("/ai/prefetch")
    def ai_prefetch_status():
        return c.prefetch.status()

    @api.post("/ai/prefetch")
    async def ai_prefetch_now():
        if c.prefetch.running:
            return {"running": True}
        asyncio.create_task(c.prefetch.run())
        return {"running": True}

    @api.post("/ai/story")
    @_ai_errors
    async def ai_story(body: AiStoryBody):
        return await c.ai.story(body.story_id, body.full_text, body.refresh)

    @api.post("/ai/briefing")
    @_ai_errors
    async def ai_briefing(body: AiBriefingBody):
        return await c.ai.briefing(body.place, body.topic, body.refresh)

    @api.get("/events")
    async def events(request: Request):
        async def gen():
            q = c.bus.subscribe()
            try:
                yield "retry: 5000\n\n"
                while not await request.is_disconnected():
                    try:
                        yield f"data: {await asyncio.wait_for(q.get(), 25)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                c.bus.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.include_router(api)

    # ---- single-page app -------------------------------------------------------------------------
    static: Path | None = c.config.static_dir
    if static and static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (static / path).resolve()
            if path and f.is_file() and static.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(static / "index.html")

    return app
