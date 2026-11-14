"""FlareSolverr as the last way to fetch: when it is used, how it is limited, and that cookies from one solve spare the next browser."""
import asyncio

import httpx
import pytest

from app.dao.fetch import solver
from app.dao.fetch.http import Fetcher, FetchResult
from tests.conftest import FakeFetcher, rss
from tests.test_ingest_db import HINDU, feed_row, item

SOLVER = "http://flare.test/v1"
CHALLENGE = b"<html><head><title>Just a moment...</title></head><body>Enable JavaScript and cookies to continue</body></html>"
FEED = rss([item("Water supply cut in Chennai on Monday", "https://www.thehindu.com/w1")])


def cfg(**kw):
    return {"url": SOLVER, "timeout": 30, "parallel": 1, "per_hour": 60, **kw}


class Site:
    """A site behind a JavaScript challenge and a FlareSolverr that solves it, as an httpx transport."""
    def __init__(self, ok=True, gate: asyncio.Event | None = None, clearance_works=True):
        self.ok, self.gate, self.clearance_works = ok, gate, clearance_works
        self.solves, self.gets = 0, []

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        if str(req.url) == SOLVER:
            self.solves += 1
            if self.gate:
                await self.gate.wait()
            if not self.ok:
                return httpx.Response(200, json={"status": "error", "message": "Challenge not solved"})
            return httpx.Response(200, json={"status": "ok", "solution": {
                "url": "https://site.test/feed", "status": 200, "response": FEED.decode(), "userAgent": "Chrome/Solved",
                "cookies": [{"name": "cf_clearance", "value": "tok", "expires": -1}, {"name": "session", "value": "s"}]}})
        self.gets.append((str(req.url), req.headers.get("cookie"), req.headers.get("user-agent")))
        if "cf_clearance=tok" in (req.headers.get("cookie") or "") and self.clearance_works:
            return httpx.Response(200, content=FEED)
        return httpx.Response(403, content=CHALLENGE, headers={"server": "cloudflare"})


async def fetcher(site, config=None):
    f = Fetcher("ua", host_delay=0)
    f.robots_on = lambda: False
    f.solver_config = lambda: config
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(site))
    return f


def test_the_marks_of_a_challenge_page_and_of_a_wrapped_feed():
    assert solver.looks_challenged(CHALLENGE) and not solver.looks_challenged(FEED)
    assert not solver.looks_challenged(b"<html>" + b"x" * 200_000 + b" just a moment</html>")       # a long page is content, whatever it says
    assert solver.worth_trying(403, None) and solver.worth_trying(503, None) and not solver.worth_trying(404, None) and not solver.worth_trying(None, "Timed out")
    wrapped = '<html><body><pre style="x">&lt;?xml version="1.0"?&gt;&lt;rss&gt;&lt;/rss&gt;</pre></body></html>'
    assert solver.unwrap(wrapped).startswith("<?xml") and solver.unwrap("<html><body>hello</body></html>").startswith("<html>")


async def test_a_refusal_is_solved_and_the_next_request_uses_the_earned_cookies():
    site = Site()
    f = await fetcher(site, cfg())
    r = await f.get("https://site.test/feed", solve=True)
    assert r.solved and r.status == 200 and r.body == FEED and site.solves == 1
    r2 = await f.get("https://site.test/feed", solve=True)
    assert r2.solved and site.solves == 1                                         # no second browser: the cookies and the browser's User-Agent were used
    assert site.gets[-1] == ("https://site.test/feed", "cf_clearance=tok; session=s", "Chrome/Solved")
    assert f.solver.stats["worked"] == 1 and f.solver.stats["reused"] == 1


async def test_cookies_a_site_refuses_are_dropped_and_not_kept_again():
    site = Site(clearance_works=False)
    f = await fetcher(site, cfg())
    await f.get("https://site.test/feed", solve=True)
    r = await f.get("https://site.test/feed", solve=True)                         # cookies refused -> the browser again (and the page is not lost)
    assert site.solves == 2 and r.status in (200, 403)
    await f.get("https://site.test/feed", solve=True)
    assert "site.test" not in f._clearance                                         # a site that refuses the cookies is solved afresh each time


async def test_nothing_happens_unless_the_setting_is_on_and_the_caller_asked():
    site = Site()
    off = await fetcher(site, None)
    assert (await off.get("https://site.test/feed", solve=True)).status == 403 and site.solves == 0
    on = await fetcher(site, cfg())
    assert (await on.get("https://site.test/feed")).status == 403 and site.solves == 0        # discovery and trust probes never reach the browser
    assert (await on.get("https://site.test/nope", solve=True)).solved                         # a refusal like HTTP 403


async def test_a_failed_solve_pauses_that_site_and_the_hourly_limit_stops_the_rest():
    site = Site(ok=False)
    f = await fetcher(site, cfg(per_hour=2))
    for _ in range(3):
        r = await f.get("https://site.test/feed", solve=True)
    assert r.status == 403 and site.solves == 1                                    # failed once: that site is left alone for six hours
    ok = await fetcher(Site(), cfg(per_hour=2))
    for i in range(3):
        await ok.get(f"https://s{i}.test/feed", solve=True)
    assert ok.solver.stats["tried"] == 2 and ok.solver.stats["capped"] == 1


async def test_a_busy_solver_defers_a_feed_check_instead_of_making_it_wait():
    gate = asyncio.Event()
    site = Site(gate=gate)
    f = await fetcher(site, cfg(parallel=1))
    first = asyncio.create_task(f.get("https://a.test/feed", solve=True))
    await asyncio.sleep(0.05)
    second = await f.get("https://b.test/feed", solve=True, defer_transient=True)
    assert second.transient and second.status == 403 and site.solves == 1
    gate.set()
    assert (await first).solved


async def test_an_unreachable_solver_costs_one_try_then_is_left_alone_for_a_while():
    def refuse(req):
        if str(req.url) == SOLVER:
            raise httpx.ConnectError("down")
        return httpx.Response(403, content=CHALLENGE)
    f = Fetcher("ua", host_delay=0)
    f.robots_on = lambda: False
    f.solver_config = lambda: cfg()
    f._client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    for i in range(3):
        assert (await f.get(f"https://h{i}.test/", solve=True)).status == 403
    assert f.solver.stats["tried"] == 1


class Walled(FakeFetcher):
    """A feed that answers 200 with a challenge page, also to a browser fingerprint; only the solver gets through."""
    def __init__(self, feeds, url, busy_first=False):
        super().__init__(feeds)
        self.url, self.busy, self.asked = url, busy_first, 0
        self.solver_config = lambda: cfg()

    async def get(self, url, etag=None, last_modified=None, *, robots=True, accept=None, user_agent=None, impersonated=False, defer_transient=False, solve=False, solved=False):
        self.calls.append(url)
        if url != self.url:
            return FetchResult(404, error="HTTP 404", final_url=url)
        if solved:
            self.asked += 1
            if self.busy and defer_transient:
                return FetchResult(None, error="Solver busy", transient=True)
            return FetchResult(200, self.feeds[url], final_url=url, solved=True)
        return FetchResult(200, CHALLENGE, final_url=url)


@pytest.mark.db
async def test_a_challenge_page_in_a_feed_check_goes_to_the_solver_and_a_busy_solver_means_a_second_try_later(container):
    c = container
    fake = Walled({HINDU: FEED}, HINDU, busy_first=True)
    c.fetcher = c.ingest.fetcher = fake
    c.repo.feeds_poll_now()
    stats = await c.ingest.run_cycle()
    assert fake.asked == 2 and stats["deferred"] >= 1                              # busy the first time (end of the queue), solved the second
    assert stats["articles_new"] == 1 and feed_row(c, HINDU)["fail_count"] == 0
