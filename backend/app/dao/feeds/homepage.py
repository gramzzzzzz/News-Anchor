"""Article links on a news site's home page, for the sites that offer neither a feed nor a news sitemap.

A home page links to many things: sections, tags, authors, pages about the site, and the latest articles. An article link is told apart
by its address and its text: on the same site; a path with a long hyphenated slug, a long number or a date; not a section, tag, author,
search, login or policy page; and link text of at least four words (the headline). Each new link seen on a later visit becomes an article
with that headline and the time it was first seen (the page gives no publication time). The first visit only records what is there."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.services.textutil import clean_url

_NOT_ARTICLE = re.compile(r"/(tag|tags|topic|topics|category|categories|author|authors|writer|search|login|signin|signup|register|subscribe|subscription|"
                          r"account|privacy|terms|cookie|cookies|contact|about|about-us|advertise|careers|jobs-at|newsletter|newsletters|rss|feed|feeds|"
                          r"page|archive|archives|gallery|galleries|photos|videos|video|live-tv|epaper|e-paper|podcasts|shop|events|weather)(/|$)", re.I)
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){3,}", re.I)          # four or more hyphenated words
_ID = re.compile(r"\d{5,}")
_DATE = re.compile(r"/20\d\d/(0?[1-9]|1[0-2])(/|$)|/20\d\d-\d\d-\d\d")
_FILE = re.compile(r"\.(jpg|jpeg|png|gif|webp|pdf|mp3|mp4|zip|css|js)$", re.I)


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "a":
            self._href, self._text = dict(attrs).get("href"), []
        elif tag == "img" and self._href is not None:
            alt = dict(attrs).get("alt") or ""
            if alt and not self._text:
                self._text.append(alt)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None

    def handle_data(self, data):
        if self._href is not None and not self._skip:
            self._text.append(data)


def _site(host: str) -> str:
    h = (host or "").lower().removeprefix("www.")
    for pre in ("m.", "amp.", "mobile."):
        if h.startswith(pre) and h.count(".") >= 2:
            h = h[len(pre):]
    return h


def _unrepeat(text: str) -> str:
    """A card's link often holds the headline twice (heading, then the same words as a teaser): keep it once."""
    w = text.split()
    for k in range(len(w) // 2, 3, -1):
        if w[:k] == w[k:2 * k]:
            return " ".join(w[:k])                       # the rest is the teaser
    return text


def _better(new: str, old: str) -> bool:
    """Of several links to one article, the headline: the longest text of at most 25 words (a teaser paragraph is longer)."""
    nw, ow = len(new.split()), len(old.split())
    if not old:
        return True
    if (nw <= 25) != (ow <= 25):
        return nw <= 25
    return nw > ow if nw <= 25 else nw < ow


def article_links(page_html: str, base_url: str, limit: int = 200) -> list[dict]:
    """[{title, url, lead, published}] for the article links of a home page, in page order, one per address (the longest text kept)."""
    p = _Anchors()
    try:
        p.feed(page_html[:1_500_000])
    except Exception:                                       # noqa: BLE001 - broken markup: keep what was read
        pass
    site = _site(urlsplit(base_url).hostname or "")
    best: dict[str, str] = {}
    order: list[str] = []
    for href, text in p.links:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        sp = urlsplit(url)
        if sp.scheme not in ("http", "https") or not (_site(sp.hostname or "") == site or _site(sp.hostname or "").endswith("." + site)):
            continue
        path = sp.path or "/"
        if path.count("/") < 1 or path in ("/", "") or _NOT_ARTICLE.search(path) or _FILE.search(path):
            continue
        if not (_SLUG.search(path) or _ID.search(path) or _DATE.search(path)):
            continue
        text = _unrepeat(text)
        words = text.split()
        if len(words) < 4 or len(text) > 300:
            continue
        u = clean_url(url)
        if u not in best:
            order.append(u)
        if _better(text, best.get(u, "")):
            best[u] = text
    return [{"title": best[u][:300], "url": u, "lead": "", "published": None} for u in order[:limit]]
