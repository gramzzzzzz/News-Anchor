"""Google News sitemaps: the file publishers make so Google News can find their articles. It lists the last two days of articles, each
with its headline and publication time (https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap). Sites say
where it is in robots.txt ("Sitemap: ..."), and many use one of a few usual addresses. A big site splits it into several files listed in
a sitemap index; then the newest files of the index that are news sitemaps are read."""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from app.dao.feeds.rss import parse_news_sitemap

PATHS = ("/news-sitemap.xml", "/sitemap_news.xml", "/sitemap-news.xml", "/news_sitemap.xml", "/sitemaps/news.xml", "/sitemap/news.xml",
         "/google-news-sitemap.xml", "/newssitemap.xml", "/sitemap_news_1.xml", "/feeds/sitemap_news.xml")
_SM = re.compile(r"<sitemap\b[^>]*>(.*?)</sitemap>", re.S | re.I)
_LOC = re.compile(r"<loc>(.*?)</loc>", re.S | re.I)
_MOD = re.compile(r"<lastmod>(.*?)</lastmod>", re.S | re.I)


def robots_sitemaps(robots_txt: str) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in robots_txt.splitlines() if line.lower().startswith("sitemap:") and "http" in line]


def newsy(url: str) -> bool:
    return bool(re.search(r"news|gnews", urlsplit(url).path, re.I))


def is_index(body: bytes) -> bool:
    return b"<sitemapindex" in body[:4000].lower()


def index_children(body: bytes) -> list[tuple[str, str]]:
    """[(address, lastmod)] of a sitemap index, newest first (no date counts as oldest)."""
    text = body.decode("utf-8", "replace")
    out = []
    for m in _SM.finditer(text):
        loc = _LOC.search(m.group(1))
        if loc:
            mod = _MOD.search(m.group(1))
            out.append((html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", loc.group(1))).strip(), mod.group(1).strip() if mod else ""))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


async def read(fetcher, url: str, body: bytes | None = None, *, children: int = 2) -> tuple[list[dict], bool]:
    """Items of a news sitemap, or of the newest news files of a sitemap index (robots.txt obeyed)."""
    if body is None:
        r = await fetcher.get(url, accept="application/xml,text/xml;q=0.9,*/*;q=0.5")
        if r.error or r.status != 200 or not r.body:
            return [], False
        body = r.body
    if not is_index(body):
        return parse_news_sitemap(body)
    kids = index_children(body)
    news_kids = [k for k, _ in kids if newsy(k)] or ([k for k, _ in kids] if newsy(url) else [])
    items, ok = [], False
    for k in news_kids[:children]:
        r = await fetcher.get(k, accept="application/xml,text/xml;q=0.9,*/*;q=0.5")
        if r.error or r.status != 200 or not r.body or is_index(r.body):
            continue
        got, rec = parse_news_sitemap(r.body)
        items += got
        ok = ok or rec
    return items, ok
