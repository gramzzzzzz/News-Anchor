import datetime as dt

import pytest

from tests.conftest import FakeFetcher, rss

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = lambda: dt.datetime.now(UTC)

HINDU = "https://www.thehindu.com/news/national/feeder/default.rss"
MINT = "https://www.livemint.com/rss/news"
BBC = "https://feeds.bbci.co.uk/news/world/rss.xml"
F1 = "https://www.formula1.com/en/latest/all.xml"
WRC = "https://www.motorsport.com/rss/wrc/news/"


def item(title, link, mins_ago=10, desc=""):
    return {"title": title, "link": link, "desc": desc, "pub": NOW() - dt.timedelta(minutes=mins_ago)}


def feed_row(c, url):
    return next(f for f in c.repo.all_feeds() if f["url"] == url)


def test_two_outlets_about_one_event_become_one_story(container):
    c = container
    n, s = c.ingest.process_items(feed_row(c, HINDU), [{**item("INDIA bloc postpones public impeachment of CEC to Oct. 31 due to rain", "https://www.thehindu.com/a1?utm_source=x"), "published": NOW(), "lead": "", "url": "https://www.thehindu.com/a1", "title": "INDIA bloc postpones public impeachment of CEC to Oct. 31 due to rain"}])
    assert (n, s) == (1, 1)
    n, s = c.ingest.process_items(feed_row(c, MINT), [{"title": "INDIA bloc postpones 'public impeachment' of CEC Gyanesh Kumar due to rain", "url": "https://www.livemint.com/b1", "lead": "", "published": NOW()}])
    assert (n, s) == (1, 0)                                   # same story, second outlet
    out = c.stories.list("world", "", "top")
    assert len(out["stories"]) == 1
    st = out["stories"][0]
    assert st["outlets"] == 2 and not st["single_source"] and {x["k"] for x in st["strip"]} == {"hindu", "mint"}
    assert st["place"]["code"] == "IN" and st["topic"] is not None
    # re-polling the same items adds nothing
    assert c.ingest.process_items(feed_row(c, HINDU), [{"title": "INDIA bloc postpones public impeachment of CEC to Oct. 31 due to rain", "url": "https://www.thehindu.com/a1?utm_campaign=y", "lead": "", "published": NOW()}]) == (0, 0)


def test_scopes_by_place_and_topic_with_ancestors(container):
    c = container
    now = NOW()
    c.ingest.process_items(feed_row(c, HINDU), [
        {"title": "Chennai police arrest former minister in schools fraud case", "url": "https://www.thehindu.com/c1", "lead": "", "published": now},
        {"title": "Parliament passes new bill after heated debate", "url": "https://www.thehindu.com/c2", "lead": "", "published": now}])
    c.ingest.process_items(feed_row(c, F1), [{"title": "Verstappen wins Mexico City Grand Prix", "url": "https://www.formula1.com/x1", "lead": "Formula 1 race report", "published": now}])
    ids = lambda **k: {s["title"] for s in c.stories.list(**{"tab": "top", **k})["stories"]}
    assert "Chennai police arrest former minister in schools fraud case" in ids(place="IN-TN-chennai", topic="")
    assert "Chennai police arrest former minister in schools fraud case" in ids(place="IN-TN", topic="")        # ancestors
    assert "Chennai police arrest former minister in schools fraud case" in ids(place="asia", topic="")
    assert "Parliament passes new bill after heated debate" not in ids(place="IN-TN-chennai", topic="")
    f1 = ids(place="world", topic="sport.motor-racing")
    assert f1 == {"Verstappen wins Mexico City Grand Prix"} and ids(place="world", topic="sport") == f1
    assert ids(place="world", topic="sport.cricket") == set()
    assert ids(place="IN", topic="sport.motor-racing") == set()                   # F1.com is international: no home-country fallback


def test_breaking_needs_many_outlets_published_close_together(container):
    c = container
    title = "Magnitude 7 earthquake strikes off the coast of Japan tsunami warning issued"
    tails = ["", " officials say", " as residents flee", " for Pacific coast"]
    sources = [("hindu", HINDU), ("mint", MINT), ("bbc", BBC)]
    for i in range(3):
        c.ingest.process_items(feed_row(c, sources[i][1]), [{"title": title + tails[i], "url": f"https://x.example/{i}", "lead": "", "published": NOW() - dt.timedelta(minutes=20 - i)}])
    st = c.stories.list("world", "", "top")["stories"][0]
    assert st["outlets"] == 3 and not st["breaking"]                        # threshold is 5
    c.settings.set_many({"breaking.outlets": 3})
    c.ingest.process_items(feed_row(c, "https://www.theguardian.com/world/rss"), [{"title": title + tails[3], "url": "https://x.example/9", "lead": "", "published": NOW() - dt.timedelta(minutes=15)}])
    st = c.stories.list("world", "", "top")["stories"][0]
    assert st["breaking"] and st["outlets"] == 4


def test_wire_copy_counts_once_for_breaking_and_independence(container):
    """Outlets printing the same wire text word for word are one report: they share a fingerprint, so they count once."""
    c = container
    c.settings.set_many({"breaking.outlets": 3})
    title, lead = "Exchange hacked as thieves take 400 million in crypto tokens", "Hackers drained hot wallets overnight, the exchange said."
    for i, url in enumerate((HINDU, MINT, BBC, "https://www.theguardian.com/world/rss")):
        c.ingest.process_items(feed_row(c, url), [{"title": title, "url": f"https://w.example/{i}", "lead": lead, "published": NOW() - dt.timedelta(minutes=10)}])
    st = c.stories.list("world", "", "top")["stories"][0]
    assert st["outlets"] == 4 and st["indep"] == 1 and not st["breaking"]


def test_outlets_stamped_with_one_instant_count_once_for_breaking(container):
    """A scheduled syndicated column goes out under the same timestamp on many sites; three or more such outlets are one report."""
    c = container
    c.settings.set_many({"breaking.outlets": 4})
    urls = (HINDU, MINT, BBC, "https://www.theguardian.com/world/rss")
    stamp = NOW() - dt.timedelta(minutes=20)
    for i, url in enumerate(urls):
        c.ingest.process_items(feed_row(c, url), [{"title": "Ask the doctor: when cancer treatment ends you learn a new routine", "url": f"https://s.example/{i}", "lead": "", "published": stamp}])
    st = c.stories.list("world", "", "top")["stories"][0]
    assert st["outlets"] == 4 and not st["breaking"]
    for i, url in enumerate(urls):                                        # real news: the same four outlets at their own moments
        c.ingest.process_items(feed_row(c, url), [{"title": "Magnitude 7 earthquake strikes off the coast of Japan tsunami warning issued" + ("", " officials say", " as residents flee", " for Pacific coast")[i], "url": f"https://q.example/{i}", "lead": "", "published": NOW() - dt.timedelta(minutes=20 - i, seconds=i * 7)}])
    quake = next(x for x in c.stories.list("world", "", "top")["stories"] if x["title"].startswith("Magnitude"))
    assert quake["breaking"]


def test_old_items_are_ignored_and_single_source_filling(container):
    c = container
    old = {"title": "Ancient headline from last month", "url": "https://x.example/old", "lead": "", "published": NOW() - dt.timedelta(days=30)}
    assert c.ingest.process_items(feed_row(c, HINDU), [old]) == (0, 0)
    c.ingest.process_items(feed_row(c, HINDU), [{"title": "Lone story about municipal water supply", "url": "https://x.example/lone", "lead": "", "published": NOW()}])
    assert c.stories.list("world", "", "top")["stories"][0]["single_source"] is True
    c.settings.set_many({"feed.single_source": False})
    assert c.stories.list("world", "", "top")["stories"] == []


async def test_cycle_polls_only_due_feeds_and_skips_disabled_specialist_feeds(container):
    c = container
    fake = FakeFetcher({HINDU: rss([item("Rain forecast for Chennai this weekend", "https://www.thehindu.com/r1")]),
                        MINT: rss([item("Rain forecast for Chennai this weekend says IMD", "https://www.livemint.com/r2")]),
                        WRC: rss([item("Rally Japan preview", "https://www.motorsport.com/w1")])})
    c.fetcher = c.ingest.fetcher = fake
    c.topics.set_enabled("sport.motor-racing", False)  # motor racing taken out of the menus: its specialist feeds wait
    assert (await c.ingest.run_cycle())["feeds_total"] == 0        # first polls are staggered over 5 minutes: nothing is due yet
    c.repo.feeds_poll_now()
    stats = await c.ingest.run_cycle()
    assert stats["articles_new"] == 2
    assert WRC not in fake.calls and F1 not in fake.calls
    assert HINDU in fake.calls                         # general feeds are always polled
    c.topics.set_enabled("sport.motor-racing", True)
    c.repo.feeds_poll_now()
    await c.ingest.run_cycle()
    assert WRC in fake.calls
    failing = [f for f in c.repo.all_feeds() if f["fail_count"] > 0]
    assert failing and all(f["last_status"] == 404 for f in failing)       # unknown URLs 404 in the fake; one bad feed never stops the cycle
    ok = feed_row(c, HINDU)
    assert ok["fail_count"] == 0 and ok["last_ok_at"] is not None and ok["etag"] == "abc"


def test_saved_following_and_favourites(container):
    c = container
    c.ingest.process_items(feed_row(c, F1), [{"title": "Norris signs new McLaren contract extension", "url": "https://www.formula1.com/n1", "lead": "", "published": NOW()}])
    sid = c.stories.list("world", "", "top")["stories"][0]["id"]
    c.repo.saved_set(sid, True)
    assert [s["id"] for s in c.stories.list("world", "", "saved")["stories"]] == [sid]
    assert c.repo.favourites() == []
    c.repo.favourite_add("world", "sport.motor-racing")
    assert [f["topic"] for f in c.repo.favourites()] == ["sport.motor-racing"]
    with pytest.raises(Exception):
        c.stories.list("world", "", "following")                                    # the Following tab is gone; favourites stay as shortcuts


def test_foreign_sport_from_a_home_outlet_is_not_home_news(container):
    c = container
    c.ingest.process_items(feed_row(c, "https://sportstar.thehindu.com/football/feeder/default.rss"),
                           [{"title": "Carragher: Apologise to Aston Villa and fans, my language was wrong", "url": "https://sportstar.thehindu.com/f1", "lead": "Earlier this week on the podcast", "published": NOW()}])
    c.ingest.process_items(feed_row(c, HINDU), [{"title": "Parliament passes new bill after heated debate", "url": "https://www.thehindu.com/p1", "lead": "", "published": NOW()}])
    titles = {s["title"] for s in c.stories.list("IN", "", "top")["stories"]}
    assert "Parliament passes new bill after heated debate" in titles and not any("Carragher" in t for t in titles)
    assert any("Carragher" in s["title"] for s in c.stories.list("world", "sport.football", "top")["stories"])


def test_routine_items_are_not_ranked_and_a_wire_copy_does_not_confirm_itself_in_latest(container):
    c = container
    for i, f in enumerate((HINDU, MINT, BBC)):
        c.ingest.process_items(feed_row(c, f), [{"title": "High school football playoff scores and highlights", "url": f"https://r.example/{i}", "lead": f"Scores from tonight {i}", "published": NOW() - dt.timedelta(minutes=30 - i * 10)}])
    assert c.stories.list("world", "", "top")["stories"] == [] and c.stories.list("world", "", "trending")["stories"] == []
    assert not c.stories.list("world", "", "latest")["stories"]                      # not in the world-wide Latest either
    assert not c.stories.list("world", "sport", "latest")["stories"]           # the same text from three outlets is one wire copy: no second independent outlet confirms it


def test_a_city_papers_feed_place_applies_only_to_local_articles(container):
    c = container
    f = next(x for x in c.repo.all_feeds() if x["place_mode"] == "explicit" and x["places"] == ["IN-TN"])
    t, p = c.ingest.tag(f, "Power cut hits several areas after transformer fire", "")
    assert "IN-TN" in p                                                        # names no place: the paper's own state
    t, p = c.ingest.tag(f, "Chennai metro extends late-night service", "")
    assert "IN-TN" in p and "IN-TN-chennai" in p                               # inside it
    t, p = c.ingest.tag(f, "5th T20I: Shreyas, Kishan score fifties but New Zealand limit India to 191/8", "")
    assert "IN-TN" not in p and "sport.cricket" in t                          # national sport: not Tamil Nadu news
    t, p = c.ingest.tag(f, "Floods in Assam displace thousands", "")
    assert "IN-TN" not in p and "IN-AS" in p                                   # somewhere else in India


def test_breaking_stories_come_first_in_top_and_one_source_counts_once(container):
    c = container
    c.settings.set_many({"breaking.outlets": 3})
    for i, url in enumerate((HINDU, MINT)):
        c.ingest.process_items(feed_row(c, url), [{"title": f"Parliament passes the long awaited water bill after debate {i}", "url": f"https://q.example/{i}", "lead": "", "published": NOW() - dt.timedelta(hours=3)}])
    t = "Strong earthquake shakes the northern coast and tsunami alert issued"
    for i, url in enumerate((HINDU, MINT, BBC)):
        c.ingest.process_items(feed_row(c, url), [{"title": t + ["", " officials say", " residents flee"][i], "url": f"https://e.example/{i}", "lead": "", "published": NOW() - dt.timedelta(minutes=10)}])
    top = c.stories.list("world", "", "top")["stories"]
    assert top[0]["breaking"] and top[0]["title"].startswith("Strong earthquake")                 # a NULL mark no longer sorts above it
    # many items of one source in one window are one outlet, not many
    items = [{"title": f"Fund number {i} buys shares of Harbour Corp in a filing today", "url": f"https://mb.example/{i}", "lead": "", "published": NOW() - dt.timedelta(minutes=5)} for i in range(5)]
    c.ingest.process_items(feed_row(c, HINDU), items)
    assert not any(s["breaking"] and s["title"].startswith("Fund number") for s in c.stories.list("world", "", "latest")["stories"])
    assert not any(s["title"].startswith("Fund number") for s in c.stories.list("world", "", "latest", 50)["stories"])   # one outlet's posts are never Latest


def test_a_surname_is_not_a_town_and_a_named_state_wins():
    from app.services.places import Gazetteer
    g = Gazetteer()
    p = g.with_ancestors(g.detect("Rajasthan Puts IPS Officer Prashakha Mathur On APO Amid Corruption Probe", "", "IN"))
    assert "IN-RJ" in p and "IN-TN" not in p
    assert "IN-TN" in g.with_ancestors(g.detect("Heavy rain in Mathur, Tamil Nadu", "", "IN"))
