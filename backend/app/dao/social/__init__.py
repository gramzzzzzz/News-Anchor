"""Social platforms. Every connector returns posts in the same shape as a parsed feed: {title, url, lead, published}.
A post's title is "@author: first words of the post"; its address is the post itself, so the platform is always the publisher shown."""
from __future__ import annotations

import datetime as dt
import re

from app.services.textutil import strip_html

UTC = dt.timezone.utc
_WS = re.compile(r"\s+")


class SocialError(Exception):
    """A connector could not read (bad login, revoked access, platform refused). The message is shown in Settings."""


def when(s: str | int | float | None) -> dt.datetime | None:
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return dt.datetime.fromtimestamp(float(s), UTC)
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def post(author: str, text: str, url: str, published, link: str = "", link_title: str = "") -> dict | None:
    """One post as a feed item. Text is cleaned of markup; the title is the author and the first sentence (at most 200 characters)."""
    body = _WS.sub(" ", strip_html(text or "")).strip()
    head = body or link_title
    if not head or not url:
        return None
    # the first sentence, but never cut at an abbreviation ("The L.A. council ...", "U.S. sanctions ..."): a sentence has 30+ characters
    m = re.search(r"(?<=[.!?])\s", head[30:])
    first = head[:30 + m.start()] if m else head
    if len(first) > 200:
        first = first[:197].rsplit(" ", 1)[0] + "..."
    lead = body if body != first else ""
    if link:
        lead = (lead + " " if lead else "") + f"Links to {link_title + ': ' if link_title and link_title not in head else ''}{link}"
    return {"title": f"@{author}: {first}"[:300], "url": url, "lead": lead[:400], "published": when(published), "link": link or ""}
