"""The robots.txt switch: on, it is fetched, matched and kept; off, none of that happens and what was kept is discarded."""
import asyncio

import httpx
import pytest

from app.container import build
from app.dao.fetch.http import Fetcher

pytestmark = pytest.mark.db
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
ROBOTS = "User-agent: NewsAnchor\nDisallow: /private\n\nUser-agent: *\nAllow: /\nSitemap: https://a.example/news-sitemap.xml\n"


def make():
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.headers["user-agent"]))
        return httpx.Response(200, text=ROBOTS if req.url.path == "/robots.txt" else "<html>ok</html>")

    f = Fetcher(UA, host_delay=0)
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": UA})
    return f, seen


def test_robots_on_fetches_matches_and_keeps_then_off_flushes_and_stops():
    async def go():
        f, seen = make()
        on = {"v": True}
        f.robots_on = lambda: on["v"]
        r = await f.get("https://a.example/private/x")
        assert r.error == "Disallowed by robots.txt"
        assert (await f.get("https://a.example/open")).status == 200
        assert [p for p, _ in seen].count("/robots.txt") == 1             # kept after the first read
        assert await f.sitemaps("https://a.example/") == ["https://a.example/news-sitemap.xml"]
        assert f._robots
        on["v"] = False
        f.flush_robots()
        assert not f._robots
        seen.clear()
        assert (await f.get("https://a.example/private/x")).status == 200   # not matched
        assert (await f.get("https://a.example/open")).status == 200
        assert [p for p, _ in seen].count("/robots.txt") == 0               # not fetched for any page, nor matched
        assert await f.sitemaps("https://a.example/") == ["https://a.example/news-sitemap.xml"]   # read for its Sitemap lines only
        assert [p for p, _ in seen].count("/robots.txt") == 1 and not f._robots                   # fresh, and not kept
        await f.sitemaps("https://a.example/")
        assert [p for p, _ in seen].count("/robots.txt") == 2 and not f._robots
        assert {ua for _, ua in seen} == {UA}
        on["v"] = True                                                      # and back on: fetched and matched again
        assert (await f.get("https://a.example/private/x")).error == "Disallowed by robots.txt"
    asyncio.run(go())


def test_setting_defaults_on_and_switching_off_flushes_the_container_fetcher(container):
    c = container
    assert c.settings.get("fetch.robots") is True and c.fetcher.robots_on()
    c.fetcher._robots["https://a.example"] = (0.0, ROBOTS)
    c.settings.set_many({"fetch.robots": False})
    assert not c.fetcher.robots_on() and not c.fetcher._robots
    c.settings.reset("fetch.robots")
    assert c.fetcher.robots_on()
