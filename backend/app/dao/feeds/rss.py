"""RSS / Atom parsing into plain dicts (feedparser tolerates the many ways real feeds are broken)."""
from __future__ import annotations

import calendar
import datetime as dt
import html
import re
from typing import Any

import feedparser

from app.services.textutil import clean_url, strip_html

UTC = dt.timezone.utc
_TAIL = re.compile(r"\s*(?:\.{3}|\u2026)?\s*(?:keep reading|read more|continue reading|read the full story)\.?\s*$|\s*The post .{3,160} appeared first on .{2,80}\.?\s*$", re.I)


_HREF = re.compile(r"""href=["'](https?://[^"'\s>]+)["']""", re.I)
_URL = re.compile(r"<url\b[^>]*>(.*?)</url>", re.S | re.I)
_TAGS = {k: re.compile(rf"<{k}[^>]*>(.*?)</{k}>", re.S | re.I) for k in ("loc", "news:title", "news:publication_date", "lastmod", "news:keywords")}


def parse_news_sitemap(body: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Google News sitemaps list the last two days of articles with title and date. Many sites offer only this."""
    text = body.decode("utf-8", "replace")
    if "<urlset" not in text[:4000].lower() or "news:news" not in text.lower():
        return [], False
    items = []
    for m in _URL.finditer(text):
        blk = m.group(1)
        get = lambda k: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (_TAGS[k].search(blk).group(1) if _TAGS[k].search(blk) else "")).strip())  # noqa: E731
        loc, title, when = get("loc"), get("news:title"), get("news:publication_date") or get("lastmod")
        if not loc.startswith("http") or not title:
            continue
        try:
            published = dt.datetime.fromisoformat(when.replace("Z", "+00:00")) if when else None
            if published and published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
        except ValueError:
            published = None
        items.append({"title": title[:300], "url": clean_url(loc), "lead": "", "published": published})
    return items, True


def parse_feed(body: bytes, base_url: str = "") -> tuple[list[dict[str, Any]], bool]:
    """Return (items, recognised). An empty but well-formed feed is recognised, not an error. Accepts RSS, Atom and Google News sitemaps."""
    if body[:6000].lower().find(b"<urlset") != -1:
        return parse_news_sitemap(body)
    f = feedparser.parse(body, response_headers={"content-location": base_url} if base_url else None)
    items = []
    for e in f.entries:
        link = (e.get("link") or "").strip()
        title = html.unescape(strip_html(e.get("title") or ""))
        if not title:                                     # posts (Mastodon) have no title: the first sentence of the text stands in
            body = html.unescape(strip_html(e.get("summary") or e.get("description") or "")).strip()
            title = re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0][:200] if body else ""
        if not link.startswith("http") or not title:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        published = dt.datetime.fromtimestamp(calendar.timegm(t), UTC) if t else None
        raw = (e.get("summary") or e.get("description") or "") + " ".join(c.get("value", "") for c in (e.get("content") or [])[:2])
        links = list(dict.fromkeys(html.unescape(u) for u in _HREF.findall(raw)))[:30]      # whom the article cites (trust: who links to whom)
        lead = html.unescape(strip_html(e.get("summary") or e.get("description") or ""))
        lead = _TAIL.sub("", lead).strip()
        if lead.lower().startswith(title.lower()[:40]) and len(lead) < len(title) + 12:
            lead = ""
        items.append({"title": title[:300], "url": clean_url(link), "lead": lead[:400], "published": published, "links": links})
    return items, bool(f.version)
