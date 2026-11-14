"""YouTube channels: every channel has a public feed of its latest videos (no key, no quota)."""
from __future__ import annotations

import re

import httpx

_ID = re.compile(r"(UC[\w-]{22})")


def feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


async def resolve(client: httpx.AsyncClient, spec: str) -> str | None:
    """A channel id from an id, a /channel/ address, an @handle or a channel page address."""
    s = spec.strip()
    m = re.fullmatch(r"UC[\w-]{22}", s) or re.search(r"/channel/(UC[\w-]{22})", s)
    if m:
        return m.group(1) if m.groups() else m.group(0)
    url = s if s.startswith("http") else f"https://www.youtube.com/{s if s.startswith('@') else '@' + s}"
    try:
        r = await client.get(url, timeout=30, follow_redirects=True, headers={"Accept-Language": "en", "Cookie": "CONSENT=YES+1"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', r.text) or re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', r.text)
    return m.group(1) if m else None


API = "https://www.googleapis.com/youtube/v3/playlistItems"


async def uploads(client: httpx.AsyncClient, key: str, channel_id: str, limit: int = 15) -> list[dict]:
    """A channel's latest videos through the YouTube Data API (one quota unit per call; a free key allows 10,000 a day).
    YouTube's robots.txt asks automated clients not to read its public channel feeds, so the API is used instead."""
    from app.dao.social import SocialError, post
    r = await client.get(API, params={"part": "snippet", "maxResults": limit, "playlistId": "UU" + channel_id[2:], "key": key}, timeout=30)
    if r.status_code in (400, 403):
        msg = ((r.json().get("error") or {}).get("message") if r.headers.get("content-type", "").startswith("application/json") else "") or f"HTTP {r.status_code}"
        raise SocialError(f"YouTube API: {msg[:150]}")
    if r.status_code == 404:
        raise SocialError("YouTube has no such channel")
    if r.status_code != 200:
        raise SocialError(f"YouTube API answered {r.status_code}")
    out = []
    for it in r.json().get("items", []):
        sn = it.get("snippet") or {}
        vid = (sn.get("resourceId") or {}).get("videoId")
        if not vid or sn.get("title") in ("Private video", "Deleted video"):
            continue
        p = post(sn.get("channelTitle") or "YouTube", sn.get("title") or "", f"https://www.youtube.com/watch?v={vid}", sn.get("publishedAt"))
        if p:
            p["title"] = (sn.get("title") or "")[:300]
            p["lead"] = (sn.get("description") or "")[:400]
            out.append(p)
    return out
