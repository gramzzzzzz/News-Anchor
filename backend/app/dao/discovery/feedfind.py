"""Find and validate a publisher's feed: advertised links on the home page, common paths, then addresses others list for it."""
from __future__ import annotations

import datetime as dt
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.dao.feeds import sitemap
from app.dao.feeds.rss import parse_feed

UTC = dt.timezone.utc
COMMON_PATHS = ("/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/rss/news.xml", "/feeds/all.rss.xml", "/atom.xml", "/index.xml", "/news/rss.xml", "/rssfeed", "/?feed=rss2")
_FEEDY = re.compile(r"rss|atom|feed|xml", re.I)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower() and a.get("href"):
            t = (a.get("type") or "").lower()
            if "rss" in t or "atom" in t:
                self.found.append((a["href"], a.get("title") or ""))


def advertised(page_html: str, base_url: str) -> list[str]:
    p = _Links()
    try:
        p.feed(page_html[:200_000])
    except Exception:
        pass
    urls = [urljoin(base_url, h) for h, _t in p.found]
    # prefer the most general: comments feeds and category feeds last
    urls.sort(key=lambda u: (bool(re.search(r"comment|category|tag|author|search", u, re.I)), len(u)))
    return list(dict.fromkeys(urls))


def common_candidates(homepage: str) -> list[str]:
    p = urlsplit(homepage)
    root = f"{p.scheme or 'https'}://{p.netloc}"
    return [root + path for path in COMMON_PATHS]


async def validate(fetcher, url: str, *, max_age_days: int = 21, min_items: int = 3) -> dict | None:
    """Fetch ``url`` (robots.txt obeyed) and accept it when it parses and has at least ``min_items`` entries with a recent one.
    Returns {url, kind, items, newest} or None."""
    r = await fetcher.get(url)
    if r.error or r.status != 200 or not r.body:
        return None
    items, recognised = parse_feed(r.body, r.final_url or url)
    if not recognised or len(items) < min_items:
        return None
    now = dt.datetime.now(UTC)
    dated = [i["published"] for i in items if i["published"]]
    newest = max(dated) if dated else None
    if newest is None or now - newest > dt.timedelta(days=max_age_days):
        return None
    kind = "sitemap" if r.body[:6000].lower().find(b"<urlset") != -1 else "rss"
    return {"url": r.final_url or url, "kind": kind, "items": len(items), "newest": newest, "titles": [i["title"] for i in items[:12]]}


async def validate_sitemap(fetcher, url: str, *, max_age_days: int = 7, min_items: int = 3) -> dict | None:
    """A Google News sitemap (or a sitemap index of them) with at least ``min_items`` articles, the newest within ``max_age_days``."""
    items, recognised = await sitemap.read(fetcher, url)
    if not recognised or len(items) < min_items:
        return None
    dated = [i["published"] for i in items if i["published"]]
    newest = max(dated) if dated else None
    if newest is None or dt.datetime.now(UTC) - newest > dt.timedelta(days=max_age_days):
        return None
    return {"url": url, "kind": "sitemap", "items": len(items), "newest": newest, "titles": [i["title"] for i in items[:12]]}


def sitemap_candidates(homepage: str, listed: list[str]) -> list[str]:
    """Where a site's news sitemap may be: those its robots.txt lists that look like news sitemaps, then the usual addresses."""
    p = urlsplit(homepage)
    root = f"{p.scheme or 'https'}://{p.netloc}"
    out = [u for u in listed if sitemap.newsy(u)] + [root + x for x in sitemap.PATHS]
    return list(dict.fromkeys(out))
