"""Mastodon and the fediverse. Public accounts and hashtags have RSS feeds (read like any feed). Your home timeline needs an
access token from your instance (Preferences, Development, New application, scope read:statuses)."""
from __future__ import annotations

import re

import httpx

from app.dao.social import SocialError, post

_ACCT = re.compile(r"^@?([A-Za-z0-9_.-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def account_rss(acct: str) -> str | None:
    """'@user@instance' (or a profile address) to the account's RSS feed."""
    a = acct.strip()
    m = _ACCT.match(a)
    if m:
        return f"https://{m.group(2)}/@{m.group(1)}.rss"
    m = re.match(r"^https://([A-Za-z0-9.-]+)/@([A-Za-z0-9_.-]+)/?$", a)
    return f"https://{m.group(1)}/@{m.group(2)}.rss" if m else None


def tag_rss(spec: str, default_instance: str = "mastodon.social") -> str | None:
    """'instance/tag', '#tag' or 'tag' to the hashtag's RSS feed on that instance."""
    s = spec.strip().lstrip("#")
    inst, _, tag = s.rpartition("/")
    inst = inst or default_instance or "mastodon.social"
    tag = re.sub(r"[^\w]", "", tag)
    return f"https://{inst}/tags/{tag}.rss" if tag and "." in inst else None


def items(statuses: list[dict]) -> list[dict]:
    out = []
    for s in statuses:
        s = s.get("reblog") or s
        card = s.get("card") or {}
        it = post((s.get("account") or {}).get("acct") or "unknown", s.get("content") or "", s.get("url") or s.get("uri") or "", s.get("created_at"),
                  card.get("url", ""), card.get("title", ""))
        if it:
            out.append(it)
    return out


async def home(client: httpx.AsyncClient, instance: str, token: str, limit: int = 40) -> list[dict]:
    inst = instance.strip().removeprefix("https://").strip("/")
    r = await client.get(f"https://{inst}/api/v1/timelines/home", params={"limit": limit}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code in (401, 403):
        raise SocialError(f"{inst} did not accept the access token")
    if r.status_code != 200:
        raise SocialError(f"{inst} answered {r.status_code}")
    return items(r.json())
