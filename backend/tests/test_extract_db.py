import json

import pytest

from app.dao.fetch.http import FetchResult
from app.services import extract as X

pytestmark = pytest.mark.db

PARA = ("The harbour authority said on Wednesday that dredging of the northern channel would begin next month and finish within eighteen weeks, "
        "adding that the contract was awarded after an open tender in which three firms took part. ")
FULL = "<html><head><title>Harbour deal</title></head><body><article><h1>Harbour deal cleared</h1>" + "".join(f"<p>{PARA}Detail {i}.</p>" for i in range(8)) + "</article></body></html>"
WALL = ('<html><head><script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "isAccessibleForFree": False, "articleBody": " ".join([PARA] * 7)}) +
        '</script></head><body><article><p>Subscribe to continue reading this article. ' + PARA + '</p></article></body></html>')
THIN = "<html><body><article><p>Subscribe to continue reading. Already a subscriber? Sign in to read.</p></article></body></html>"


class Pages:
    ua = "t/0"

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def configure(self, *a): ...
    async def start(self): ...

    async def get(self, url, etag=None, last_modified=None, *, robots=True, accept=None, user_agent=None, impersonated=False, solve=False, solved=False):
        self.calls.append(url)
        for k, v in self.pages.items():
            if url.startswith(k):
                return FetchResult(v[0], v[1].encode() if v[1] else None, error=None if v[0] == 200 else f"HTTP {v[0]}", final_url=url)
        return FetchResult(404, error="HTTP 404", final_url=url)


def art(url="https://pub.example/a1"):
    return {"url": url, "url_hash": "h-" + url, "title": "Harbour deal cleared", "lead": "The cabinet cleared a harbour deal."}


async def test_clean_page_gives_full_text_and_caches(container):
    c = container
    c.extract.fetcher = Pages({"https://pub.example/a1": (200, FULL)})
    r = await c.extract.read(art())
    assert r["status"] == "full" and r["method"] == "page" and r["words"] > 180 and len(r["paragraphs"]) >= 8
    n = len(c.extract.fetcher.calls)
    again = await c.extract.read(art())
    assert again["paragraphs"] == r["paragraphs"] and len(c.extract.fetcher.calls) == n            # served from the cache, no second request


async def test_paywalled_page_uses_only_what_the_publisher_declares_in_its_page_data(container):
    c = container
    c.extract.fetcher = Pages({"https://pub.example/w": (200, WALL)})
    r = await c.extract.read(art("https://pub.example/w"))
    assert r["method"] == "page data" and r["words"] > 100              # what the publisher itself declares for search engines
    assert not any("archive.org" in u for u in c.extract.fetcher.calls)


async def test_blocked_or_thin_page_falls_back_to_the_summary_and_says_so(container):
    c = container
    c.settings.set_many({"extract.archive_fallback": False})
    c.extract.fetcher = Pages({"https://pub.example/403": (403, ""), "https://pub.example/thin": (200, THIN)})
    r = await c.extract.read(art("https://pub.example/403"))
    assert r["status"] == "summary" and r["paragraphs"] == ["The cabinet cleared a harbour deal."] and "could not be read" in r["note"]
    r = await c.extract.read(art("https://pub.example/thin"))
    assert r["status"] == "summary" and "paywall" in r["note"].lower()


async def test_archive_copy_is_used_when_enabled_and_labelled(container):
    c = container
    snap = {"archived_snapshots": {"closest": {"available": True, "url": "http://web.archive.org/web/2026/https://pub.example/gone"}}}
    c.extract.fetcher = Pages({"https://pub.example/gone": (404, ""), "https://archive.org/wayback/available": (200, json.dumps(snap)), "https://web.archive.org/web/2026": (200, FULL)})
    r = await c.extract.read(art("https://pub.example/gone"))
    assert r["status"] == "full" and r["method"] == "archive copy" and r["archive_url"].startswith("http://web.archive.org") and "Internet Archive" in r["note"]
    c.settings.set_many({"extract.archive_fallback": False})
    r2 = await c.extract.read(art("https://pub.example/gone2"))
    assert r2["status"] == "summary"


def test_structured_text_and_paragraphs():
    assert X.words(X.structured_text(WALL)) > 150
    assert X.to_paragraphs("short\n\nA proper paragraph that is long enough to keep.\nAnother long enough paragraph follows here.") == [
        "A proper paragraph that is long enough to keep.", "Another long enough paragraph follows here."]
