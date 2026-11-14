"""Media Cloud (search.mediacloud.org) API client: the source directory, and statistics for the Explore page.

Directory calls are free. Search calls count against the owner's weekly quota (10,000 hits on a free account) and the
service allows about two queries a minute, so every call here goes through one pacing lock."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

BASE = "https://search.mediacloud.org/api/"


class MediaCloudError(Exception):
    pass


class MediaCloud:
    def __init__(self, token: str, user_agent: str, *, directory_gap: float = 1.0, search_gap: float = 31.0, client: httpx.AsyncClient | None = None):
        self.token = token
        self.directory_gap, self.search_gap = directory_gap, search_gap
        self._client = client or httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Token {token}", "Accept": "application/json", "User-Agent": user_agent},
                                                   timeout=60, follow_redirects=True)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None, *, gap: float | None = None) -> Any:
        async with self._lock:
            wait = self._last + (self.directory_gap if gap is None else gap) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            for attempt in range(3):
                try:
                    r = await self._client.get(path, params=params)
                except httpx.HTTPError as e:
                    if attempt == 2:
                        raise MediaCloudError(f"{type(e).__name__}") from e
                    await asyncio.sleep(3)
                    continue
                self._last = time.monotonic()
                if r.status_code == 429:
                    await asyncio.sleep(min(60, 10 * (attempt + 1)))
                    continue
                if r.status_code in (401, 403):
                    raise MediaCloudError("Media Cloud rejected the token")
                if r.status_code >= 400:
                    raise MediaCloudError(f"HTTP {r.status_code}")
                return r.json()
            raise MediaCloudError("rate limited")

    # ---- directory -------------------------------------------------------------------------------
    async def profile(self) -> dict:
        return await self._get("auth/profile")

    async def _pages(self, path: str, params: dict, max_pages: int = 100) -> list[dict]:
        out: list[dict] = []
        p = {**params, "limit": 250, "offset": 0}
        for _ in range(max_pages):
            d = await self._get(path, p)
            out += d.get("results", [])
            if not d.get("next"):
                break
            p["offset"] += 250
        return out

    async def national_collections(self, include_local: bool = False) -> list[dict]:
        rows = await self._pages("sources/collections/", {"platform": "online_news", "name": "National"})
        if include_local:
            rows += await self._pages("sources/collections/", {"platform": "online_news", "name": "State & Local"})
        return [r for r in rows if r["name"].endswith((" - National", " - State & Local"))]

    async def collection_sources(self, collection_id: int) -> list[dict]:
        return await self._pages("sources/sources/", {"collection_id": collection_id, "platform": "online_news"})

    async def source_feeds(self, source_id: int) -> list[dict]:
        d = await self._get("sources/feeds/", {"source_id": source_id, "limit": 50})
        return d.get("results", [])

    async def search_source(self, name: str) -> list[dict]:
        """Directory entries whose name contains ``name`` (a domain), with country, state, language and media type."""
        d = await self._get("sources/sources/", {"name": name, "limit": 5})
        return d.get("results", [])

    # ---- search (counts against the quota) -------------------------------------------------------
    async def search(self, endpoint: str, params: dict) -> Any:
        return await self._get(f"search/{endpoint}", params, gap=self.search_gap)
