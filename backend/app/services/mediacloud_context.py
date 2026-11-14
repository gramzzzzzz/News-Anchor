"""Optional global context from Media Cloud for the Explore page: how much the world's online news archive wrote about a query.
Media Cloud allows about two searches a minute and 10,000 hits a week on a free account, so a request runs in the background
(about a minute and a half for three searches), is cached for six hours, and is refused once the weekly budget set in Settings is used."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging

from app.dao.discovery.mediacloud import MediaCloudError

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
_running: set[str] = set()
CACHE_HOURS = 6


async def _home_collection(mc, country_name: str) -> int | None:
    rows = await mc._get("sources/collections/", {"name": f"{country_name} - National", "platform": "online_news", "limit": 5})
    for r in rows.get("results", []):
        if r["name"] == f"{country_name} - National":
            return r["id"]
    return None


async def context(c, q: str, days: int, collection_id: int | None) -> dict:
    q = q.strip()
    if len(q) < 2:
        raise ValueError("Type at least two characters")
    token = c.discovery.token()
    if not token:
        raise ValueError("Add a Media Cloud API token in Settings → Sources to use this")
    days = max(1, min(int(days), 365))
    key = "mc_ctx:" + hashlib.sha1(f"{q}|{days}|{collection_id}".encode()).hexdigest()[:16]
    cached = await asyncio.to_thread(c.repo.kv_get, key)
    if cached and dt.datetime.now(UTC) - dt.datetime.fromisoformat(cached["at"]) < dt.timedelta(hours=CACHE_HOURS):
        return {"state": "done", **cached}
    if key in _running:
        return {"state": "running"}
    _running.add(key)
    asyncio.create_task(_fetch(c, key, token, q, days, collection_id))
    return {"state": "running"}


async def _fetch(c, key: str, token: str, q: str, days: int, collection_id: int | None) -> None:
    mc = c.discovery.mc_factory(token)
    try:
        prof = await mc.profile()
        quota = prof.get("quota", {})
        budget = int(c.settings.get("mediacloud.weekly_budget"))
        if quota.get("hits", 0) + 3 > min(budget, quota.get("limit", 10000)):
            await asyncio.to_thread(c.repo.kv_set, key, {"at": dt.datetime.now(UTC).isoformat(), "error": f"The weekly Media Cloud budget ({budget}) is used up.", "quota": quota})
            return
        if collection_id is None:
            from app.services.discovery import DiscoveryService
            name = c.discovery._country_name(c.settings.get("home.country"))
            collection_id = await _home_collection(mc, name)
        end = dt.date.today()
        params = {"q": q, "start": (end - dt.timedelta(days=days)).isoformat(), "end": end.isoformat(), "platform": "onlinenews-mediacloud"}
        if collection_id:
            params["cs"] = str(collection_id)
        total = await mc.search("total-count", params)
        over = await mc.search("count-over-time", params)
        srcs = await mc.search("sources", {**params, "limit": 15})
        prof2 = await mc.profile()
        out = {"at": dt.datetime.now(UTC).isoformat(), "query": q, "days": days, "collection_id": collection_id,
               "count": total.get("count"), "timeline": (over.get("count_over_time") or {}).get("counts", []), "sources": srcs.get("sources", []),
               "quota": prof2.get("quota")}
        await asyncio.to_thread(c.repo.kv_set, key, out)
    except MediaCloudError as e:
        await asyncio.to_thread(c.repo.kv_set, key, {"at": dt.datetime.now(UTC).isoformat(), "error": f"Media Cloud: {e}"})
    except Exception as e:                                       # noqa: BLE001
        log.exception("Media Cloud context failed")
        await asyncio.to_thread(c.repo.kv_set, key, {"at": dt.datetime.now(UTC).isoformat(), "error": f"{type(e).__name__}"})
    finally:
        _running.discard(key)
        await mc.close()
