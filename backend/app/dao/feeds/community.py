"""Community and editor-curated link sites (Hacker News, Techmeme, Slashdot, Tildes ...): their feeds list stories picked from many
publishers. Each item is turned into the publisher's article: its own address, not the community's page. Where the item links to the
community's own page (Techmeme, Mediagazer, memeorandum and Slashdot link their headline pages), the first link in the item's text that
leaves the community is the article. Items with no outside link (discussion posts, "Ask HN") are dropped."""
from __future__ import annotations

import calendar
import datetime as dt
import html as htmllib
import re
from urllib.parse import urljoin, urlsplit

import feedparser

from app.services.textutil import clean_url, strip_html

UTC = dt.timezone.utc
_HREF = re.compile(r"""<a\s[^>]*href=["']([^"']+)["']""", re.I)
# sites that host discussion of links, never the article itself
_NOT_ARTICLE_HOSTS = ("news.ycombinator.com", "reddit.com", "redd.it", "twitter.com", "x.com", "facebook.com", "t.co", "bit.ly", "youtube.com/redirect",
                      "web.archive.org", "archive.ph", "archive.today", "archive.is")


def _site(host: str) -> str:
    return (host or "").lower().removeprefix("www.")


_PROFILE = re.compile(r"^/(u|c|m|post|comment|user|users|profile|@[^/]+)(/|$)", re.I)


def _outside(url: str, own: str) -> bool:
    """A link to an article on another site: not the community itself, not a discussion site, not a home page or a fediverse profile."""
    sp = urlsplit(url)
    h = _site(sp.hostname or "")
    if not h or h == own or h.endswith("." + own) or any(h == x or h.endswith("." + x) for x in _NOT_ARTICLE_HOSTS):
        return False
    return len(sp.path or "") > 1 and not _PROFILE.search(sp.path or "")


def parse(body: bytes, feed_url: str, community_site: str = "") -> tuple[list[dict], bool]:
    """(items, recognised): items are {title, url, lead, published} with the publisher's address."""
    f = feedparser.parse(body)
    own = _site(community_site or urlsplit(feed_url).hostname or "")
    if own.startswith("rss.") or own.startswith("feeds."):
        own = own.split(".", 1)[1]
    out = []
    for e in f.entries:
        title = strip_html(e.get("title") or "").strip()
        link = e.get("link") or ""
        if not title or not link:
            continue
        url = link if _outside(link, own) else None
        if url is None:
            text = " ".join([e.get("summary") or ""] + [c.get("value", "") for c in e.get("content", []) or []])
            for h in _HREF.findall(text):
                h = htmllib.unescape(urljoin(link, h))
                if h.startswith(("http://", "https://")) and _outside(h, own):
                    url = h
                    break
        if not url:
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        when = dt.datetime.fromtimestamp(calendar.timegm(ts), UTC) if ts else None
        title = re.sub(r"\s*\((?:[a-z0-9-]+\.)+[a-z]{2,}\)\s*$", "", title)            # "Headline (example.com)"
        title = re.sub(r"\s*\([A-Z][^()]{1,60}/[^()]{1,60}\)\s*$", "", title)          # Techmeme: "Headline (Author/Publication)"
        out.append({"title": title[:300], "url": clean_url(url), "lead": "", "published": when})
    return out, bool(f.version)
