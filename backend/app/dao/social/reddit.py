"""Reddit. With your own account: a 'script' app's client id and secret plus your username and password (OAuth password grant)
read your front page and subreddits at 100 requests a minute. Without a login, subreddit RSS feeds work until Reddit ends them
(announced for 13 November 2026). Reddit stopped approving new API apps on 31 October 2026, so only an app you already have works."""
from __future__ import annotations

import re

import httpx

from app.dao.social import SocialError, post

TOKEN = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"


def sub_name(s: str) -> str:
    return re.sub(r"^(https?://(www\.)?reddit\.com)?/?r/", "", s.strip()).strip("/").split("/")[0]


def rss_url(sub: str) -> str:
    return f"https://www.reddit.com/r/{sub_name(sub)}/.rss"


def user_agent(username: str) -> str:
    return f"server:news-anchor:0.8 (by /u/{username or 'unknown'})"          # the form Reddit asks API clients to use


async def token(client: httpx.AsyncClient, client_id: str, secret: str, username: str, password: str) -> dict:
    r = await client.post(TOKEN, auth=(client_id, secret), data={"grant_type": "password", "username": username, "password": password},
                          headers={"User-Agent": user_agent(username)}, timeout=30)
    if r.status_code in (400, 401, 403):
        raise SocialError("Reddit did not accept the app id, secret, username or password")
    d = r.json() if r.status_code == 200 else {}
    if r.status_code != 200 or "access_token" not in d:
        raise SocialError(f"Reddit answered {r.status_code}{': ' + d.get('error', '') if d.get('error') else ''}")
    return d


def items(listing: dict) -> list[dict]:
    out = []
    for ch in (listing.get("data") or {}).get("children", []):
        d = ch.get("data") or {}
        if d.get("stickied") or d.get("over_18"):
            continue
        link = "" if d.get("is_self") else (d.get("url_overridden_by_dest") or d.get("url") or "")
        text = d.get("title") or ""
        it = post(f"r/{d.get('subreddit', '?')}", text, f"https://www.reddit.com{d.get('permalink', '')}" if d.get("permalink") else "", d.get("created_utc"),
                  link if link.startswith("http") and "reddit.com" not in link else "", "")
        if it:
            it["title"] = text[:300]                                                  # a Reddit post's title is its headline
            it["lead"] = (f"r/{d.get('subreddit')} · " + ((d.get("selftext") or "")[:300] or it["lead"])).strip(" ·")[:400]
            out.append(it)
    return out


async def listing(client: httpx.AsyncClient, access: str, path: str, username: str, limit: int = 50) -> list[dict]:
    r = await client.get(API + path, params={"limit": limit, "raw_json": 1}, headers={"Authorization": f"Bearer {access}", "User-Agent": user_agent(username)}, timeout=30)
    if r.status_code == 401:
        raise SocialError("expired")
    if r.status_code != 200:
        raise SocialError(f"Reddit answered {r.status_code} for {path}")
    return items(r.json())
