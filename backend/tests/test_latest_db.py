"""Latest: a story is listed when enough different outlets reported it at different times (not one outlet, an advertisement, a wire copy or
posts placed at one moment); the clean-up of stories only one outlet reported (playbook 20.28)."""
import datetime as dt

import pytest

from app.services.spam import looks_like_ad

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = lambda: dt.datetime.now(UTC)
EVENT = "Strong earthquake shakes the northern coast and tsunami alert issued"
SUFFIX = ["", " officials say", " residents flee", " as aftershocks continue", " says geological agency"]


def feed(c, key):
    return next(f for f in c.repo.all_feeds() if f["source_key"] == key)


def put(c, key, title, url, minutes_ago, lead=""):
    c.ingest.process_items(feed(c, key), [{"title": title, "url": url, "lead": lead, "published": NOW() - dt.timedelta(minutes=minutes_ago)}])


@pytest.fixture
def c(container):
    for d in range(4):
        container.repo.ensure_partition(dt.date.today() - dt.timedelta(days=d))
    container.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2})
    return container


def latest(c, **kw):
    return c.stories.list("world", "", "latest", 20)["stories"]


def test_one_outlet_is_not_listed_but_three_outlets_at_different_times_are(c):
    put(c, "bbc", EVENT, "https://lt/1", 30)
    assert latest(c) == []
    put(c, "dw", EVENT + SUFFIX[1], "https://lt/2", 24)
    assert latest(c) == []                                              # two outlets: not yet (three are needed)
    put(c, "france24", EVENT + SUFFIX[2], "https://lt/3", 18)
    rows = latest(c)
    assert len(rows) == 1 and rows[0]["title"].startswith("Strong earthquake")
    q = rows[0]["qualified_at"]
    assert NOW() - q < dt.timedelta(minutes=20) and NOW() - q > dt.timedelta(minutes=15)       # the moment of the third outlet, not the first report


def test_the_newest_to_qualify_comes_first(c):
    for i, k in enumerate(("bbc", "dw", "france24")):
        put(c, k, "Parliament passes the long awaited water bill after debate" + SUFFIX[i], f"https://lt/a{i}", 100 - i * 10)
    for i, k in enumerate(("nyt", "npr", "pbs")):
        put(c, k, EVENT + SUFFIX[i], f"https://lt/b{i}", 40 - i * 10)
    assert [r["title"][:6] for r in latest(c)] == ["Strong", "Parlia"]


def test_posts_placed_at_one_moment_count_once_until_enough_outlets_report(c):
    same = NOW() - dt.timedelta(minutes=20)
    for i, k in enumerate(("bbc", "dw", "france24", "nyt")):
        c.ingest.process_items(feed(c, k), [{"title": EVENT + SUFFIX[i], "url": f"https://lt/s{i}", "lead": "", "published": same}])
    assert latest(c) == []                                              # four outlets, one instant: counts as one
    c.settings.set_many({"latest.sure_outlets": 4})
    assert len(latest(c)) == 1                                          # enough different outlets are listed without the timing test


def test_wire_copy_counts_once(c):
    for i, k in enumerate(("bbc", "dw", "france24")):
        put(c, k, EVENT, f"https://lt/w{i}", 30 - i * 10, lead="The agency said the quake measured 7.1 and struck at dawn near the coast.")
    assert latest(c) == []


def test_an_advertisement_is_not_listed_however_many_sites_carry_it(c):
    for i, k in enumerate(("bbc", "dw", "france24")):
        put(c, k, "Best Black Friday deals on laptops and phones this weekend" + SUFFIX[i], f"https://lt/ad{i}", 30 - i * 10)
    assert latest(c) == []
    assert looks_like_ad("Use promo code SAVE20 today") and not looks_like_ad("Stocks fall 5% off highs as the Fed holds")


def test_latest_obeys_its_window_and_the_language_setting(c):
    for i, k in enumerate(("bbc", "dw", "france24")):
        put(c, k, EVENT + SUFFIX[i], f"https://lt/l{i}", 30 - i * 10)
    c.repo.run("UPDATE stories SET lang = 'fr'")
    assert latest(c) == []
    c.settings.set_many({"feed.languages_shown": ["all"]})
    assert len(latest(c)) == 1
    c.settings.set_many({"latest.hours": 1})
    c.repo.run("UPDATE articles SET published_at = published_at - interval '3 hours'")
    assert latest(c) == []                                              # it qualified more than an hour ago


def age(c, hours):
    c.repo.run("UPDATE stories SET first_seen = first_seen - make_interval(hours => %s), last_update = last_update - make_interval(hours => %s)", (hours, hours))


def test_clean_up_deletes_what_only_one_outlet_reported_and_nothing_else(c):
    put(c, "bbc", "Local council debates the new parking charges on the high street", "https://cu/1", 5)                 # one outlet, old: goes
    put(c, "nyt", "Mayor opens the renovated harbour promenade after two years of work", "https://cu/2", 5)             # one outlet, saved: stays
    for i, k in enumerate(("bbc", "dw")):
        put(c, k, EVENT + SUFFIX[i], f"https://cu/e{i}", 5)                                                           # two outlets: stays
    put(c, "pbs", "Sponsored: Ten ways to save on your winter heating bill this year", "https://cu/ad", 5)               # an advertisement: goes
    sid = c.repo.one("SELECT id FROM stories WHERE title LIKE 'Mayor%'")["id"]
    c.repo.run("INSERT INTO saved (story_id) VALUES (%s)", (sid,))
    age(c, 100)
    put(c, "cnbc", "A fresh single report about the evening markets and bond yields", "https://cu/new", 5)              # one outlet but young: stays
    assert c.storage.purge_wait_hours() >= 84
    acts = c.storage.purge_uncorroborated()
    left = {r["title"][:12] for r in c.repo.q("SELECT title FROM stories")}
    assert left == {"Mayor opens ", "Strong earth", "A fresh sing"}
    assert c.repo.one("SELECT count(*) AS n FROM articles WHERE url LIKE 'https://cu/%%'")["n"] == 4        # the deleted stories' articles went too
    st = c.repo.kv_get("purge_stats")
    assert st["stories"] == 2 and st["ads"] == 1 and acts and "2 stories" in acts[0]
    assert c.repo.purge_uncorroborated(84)["stories"] == 0                                                      # nothing more to delete


def test_a_single_outlet_story_in_a_storyline_with_a_bigger_story_stays(c):
    for i, k in enumerate(("bbc", "dw")):
        put(c, k, "Israel election count gives Netanyahu bloc 53 seats short of majority" + SUFFIX[i], f"https://th/{i}", 50 * 60)
    put(c, "france24", "Netanyahu bloc 53 seats short of majority as Israel election count ends", "https://th/3", 2)
    rows = c.repo.q("SELECT id, thread_id, outlets FROM stories ORDER BY first_seen")
    assert len(rows) == 2 and rows[0]["thread_id"] == rows[1]["thread_id"] and rows[1]["outlets"] == 1
    age(c, 100)
    assert c.repo.purge_uncorroborated(84)["stories"] == 0


def test_the_wait_is_never_shorter_than_the_age_limit_of_feed_items(c):
    c.settings.set_many({"storage.purge_wait_hours": 24, "refresh.max_item_age_hours": 72})
    assert c.storage.purge_wait_hours() == 84
    c.settings.set_many({"storage.purge_wait_hours": 200})
    assert c.storage.purge_wait_hours() == 200
