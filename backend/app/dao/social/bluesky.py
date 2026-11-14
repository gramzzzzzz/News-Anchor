"""Bluesky (AT Protocol). Public author feeds need no login (public.api.bsky.app). Your timeline and post search need a login:
your handle and an app password (Settings, Privacy and security, App passwords on bsky.app), never your main password."""
from __future__ import annotations

import httpx

from app.dao.social import SocialError, post

PDS = "https://bsky.social/xrpc/"
PUBLIC = "https://public.api.bsky.app/xrpc/"


async def session(client: httpx.AsyncClient, handle: str, app_password: str) -> dict:
    """{accessJwt, refreshJwt, did, handle}"""
    r = await client.post(PDS + "com.atproto.server.createSession", json={"identifier": handle.strip().lstrip("@"), "password": app_password}, timeout=30)
    if r.status_code in (400, 401):
        raise SocialError("Bluesky did not accept the handle and app password")
    if r.status_code != 200:
        raise SocialError(f"Bluesky answered {r.status_code}")
    return r.json()


async def refresh(client: httpx.AsyncClient, refresh_jwt: str) -> dict:
    r = await client.post(PDS + "com.atproto.server.refreshSession", headers={"Authorization": f"Bearer {refresh_jwt}"}, timeout=30)
    if r.status_code != 200:
        raise SocialError("Bluesky session expired")
    return r.json()


def items(feed: list[dict]) -> list[dict]:
    out = []
    for f in feed:
        p = f.get("post") or f
        rec = p.get("record") or {}
        author = (p.get("author") or {}).get("handle") or "unknown"
        uri = p.get("uri") or ""
        if not uri:
            continue
        ext = ((p.get("embed") or {}).get("external") or {})
        it = post(author, rec.get("text") or "", f"https://bsky.app/profile/{author}/post/{uri.rsplit('/', 1)[-1]}", rec.get("createdAt") or p.get("indexedAt"),
                  ext.get("uri", ""), ext.get("title", ""))
        if it:
            out.append(it)
    return out


async def _get(client: httpx.AsyncClient, base: str, method: str, params: dict, jwt: str | None) -> dict:
    r = await client.get(base + method, params=params, headers={"Authorization": f"Bearer {jwt}"} if jwt else None, timeout=30)
    if r.status_code == 401:
        raise SocialError("expired")
    if r.status_code != 200:
        raise SocialError(f"Bluesky answered {r.status_code} for {method}")
    return r.json()


async def timeline(client: httpx.AsyncClient, jwt: str, limit: int = 50) -> list[dict]:
    return items((await _get(client, PDS, "app.bsky.feed.getTimeline", {"limit": limit}, jwt)).get("feed", []))


async def author_feed(client: httpx.AsyncClient, actor: str, limit: int = 30) -> list[dict]:
    d = await _get(client, PUBLIC, "app.bsky.feed.getAuthorFeed", {"actor": actor.strip().lstrip("@"), "limit": limit, "filter": "posts_no_replies"}, None)
    return items(d.get("feed", []))


async def search(client: httpx.AsyncClient, jwt: str, q: str, limit: int = 30) -> list[dict]:
    return items((await _get(client, PDS, "app.bsky.feed.searchPosts", {"q": q, "limit": limit, "sort": "latest"}, jwt)).get("posts", []))
