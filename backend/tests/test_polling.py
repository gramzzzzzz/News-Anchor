"""Feed check intervals (backing off, the daily re-analysis), the second try for slow feeds, and a 200 that is not the content."""
import datetime as dt

import pytest

from app.dao.fetch.http import FetchResult
from app.services.ingest import interval_bounds, next_interval
from tests.conftest import FakeFetcher, rss
from tests.test_ingest_db import HINDU, MINT, NOW, feed_row, item

UTC = dt.timezone.utc


def test_quiet_feeds_back_off_by_half_each_time_but_never_past_two_hours():
    base = 1800
    assert interval_bounds(base) == (300, 7200)
    iv, seen = base, []
    for _ in range(10):
        iv = next_interval(iv, base, False, True)
        seen.append(iv)
    assert seen[0] == 2700 and seen[1] == 4050 and seen[-1] == 7200       # x1.5 each quiet check, then the two-hour ceiling
    assert interval_bounds(14400 * 1)[1] == 14400                         # a base the owner set longer than two hours is respected


def test_busy_feeds_speed_up_to_a_sixth_of_the_base_and_failures_double_up_to_six_hours():
    iv = 1800
    for _ in range(10):
        iv = next_interval(iv, 1800, True, True)
    assert iv == 300                                                        # never under five minutes
    assert next_interval(900, 1800, False, False) == 3600
    assert next_interval(20000, 1800, False, False) == 21600
    assert next_interval(300, 1800, False, True, "homepage") >= 1800


@pytest.mark.db
def test_the_daily_reanalysis_resets_intervals_from_the_weeks_articles(container):
    c = container
    busy, quiet = feed_row(c, HINDU), feed_row(c, MINT)
    items = [{"title": f"Distinct story number {i} about topic{i} in the city council", "url": f"https://x.example/{i}", "lead": "", "published": NOW()} for i in range(60)]
    c.ingest.process_items(busy, items)
    for f in (busy, quiet):
        c.repo.feed_polled(f["id"], status=200, error=None, new_items=0, items_seen=0, etag=None, last_modified=None, interval_s=7200, ok=True, state="active")
    res = c.repo.feeds_reanalyse(300, 7200)
    assert res["changed"] >= 1
    b, q = feed_row(c, HINDU), feed_row(c, MINT)
    assert b["poll_interval_s"] < 7200 and b["poll_interval_s"] >= 300       # many articles: checked more often again
    assert q["poll_interval_s"] == 7200                                       # none in the week: the ceiling, never dropped
    c.repo.feed_polled(q["id"], status=200, error=None, new_items=0, items_seen=0, etag=None, last_modified=None, interval_s=300, ok=True, state="active")
    c.repo.feeds_reanalyse(300, 7200)
    assert feed_row(c, MINT)["poll_interval_s"] == 7200                       # a feed slowed by mistake is set right too


class Flaky(FakeFetcher):
    """The first request for a URL times out; the second works. Another URL answers 200 with a web page, and a browser fingerprint gets the feed."""
    def __init__(self, feeds, flaky, shell):
        super().__init__(feeds)
        self.flaky, self.shell, self.tries = flaky, shell, {}
        self.impersonate_profile = lambda: "chrome116"

    async def get(self, url, etag=None, last_modified=None, *, robots=True, accept=None, user_agent=None, impersonated=False, defer_transient=False, solve=False, solved=False):
        self.calls.append(url)
        self.tries[url] = self.tries.get(url, 0) + 1
        if url == self.flaky and self.tries[url] == 1:
            return FetchResult(None, error="Timed out", transient=defer_transient)
        if url == self.shell and not impersonated:
            return FetchResult(200, b"<html><body>Please enable JavaScript to continue</body></html>", final_url=url)
        if url in self.feeds:
            return FetchResult(200, self.feeds[url], etag="abc", final_url=url, impersonated=impersonated)
        return FetchResult(404, error="HTTP 404", final_url=url)


@pytest.mark.db
async def test_a_slow_feed_waits_at_the_end_of_the_queue_and_a_non_feed_page_is_fetched_again_as_a_browser(container):
    c = container
    fake = Flaky({HINDU: rss([item("Water supply cut in Chennai on Monday", "https://www.thehindu.com/w1")]),
                  MINT: rss([item("Markets close higher on rate hopes today", "https://www.livemint.com/m1")])}, flaky=HINDU, shell=MINT)
    c.fetcher = c.ingest.fetcher = fake
    c.repo.feeds_poll_now()
    stats = await c.ingest.run_cycle()
    assert fake.tries[HINDU] == 2 and stats["deferred"] >= 1 and stats["retried_ok"] >= 1       # timed out, tried again at the end, worked
    assert feed_row(c, HINDU)["fail_count"] == 0                                                  # the first timeout was not counted as a failure
    assert stats["unusable_retried"] == 1 and feed_row(c, MINT)["fail_count"] == 0               # the page that was not a feed came right as a browser
    assert stats["articles_new"] == 2
