"""Developments and threads, fair ranking by the recent coverage with decay, fresh and ongoing views, event status (playbook 20.11, 20.25, 20.27)."""
import datetime as dt

import pytest

from app.services.textutil import title_key

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = lambda: dt.datetime.now(UTC)
US = ["nyt", "npr", "pbs", "cnbc", "marketwatch"]
WORLD = ["bbc", "dw", "france24", "abc-au", "cbc", "rnz", "nikkei", "scmp", "straitstimes", "yonhap", "hindu"]


def feed(c, key):
    return next(f for f in c.repo.all_feeds() if f["source_key"] == key)


def put(c, key, title, url, hours_ago, lead=""):
    """One article through the normal intake (story matching, developments, threads)."""
    c.ingest.process_items(feed(c, key), [{"title": title, "url": url, "lead": lead, "published": NOW() - dt.timedelta(hours=hours_ago)}])


@pytest.fixture
def c(container):
    container.repo.ensure_partition(dt.date.today())
    container.repo.ensure_partition(dt.date.today() - dt.timedelta(days=1))
    container.repo.ensure_partition(dt.date.today() - dt.timedelta(days=2))
    container.settings.set_many({"search.embeddings": "hashed", "feed.min_outlets": 2})
    return container


def titles(c, tab, view="all", place="world", topic=""):
    return [s["title"] for s in c.stories.list(place, topic, tab, 20, view=view)["stories"]]


def test_a_later_event_of_the_same_storyline_is_a_new_development_in_the_same_thread(c):
    put(c, "bbc", "Israel election count gives Netanyahu bloc 53 seats short of majority", "https://t/1", 50)
    put(c, "dw", "Israel election count gives Netanyahu bloc 53 seats, short of majority", "https://t/2", 49)
    put(c, "france24", "Netanyahu bloc 53 seats short of majority as Israel election count ends", "https://t/3", 2)    # two days later: a new event
    rows = c.repo.q("SELECT id, thread_id, articles FROM stories ORDER BY first_seen")
    assert len(rows) == 2 and rows[0]["thread_id"] == rows[1]["thread_id"] == rows[0]["id"]
    d = c.stories.detail(rows[1]["id"])
    assert d["thread"]["developments"] == 2 and [x["current"] for x in d["timeline"]] == [True, False]       # newest first
    assert d["articles_list"][0]["url"] == "https://t/3"


def test_fresh_top_ranks_breadth_across_countries_over_one_countrys_volume(c):
    # one domestic story carried by five US outlets, one story carried by one outlet in each of five other countries
    for i, k in enumerate(US):
        put(c, k, f"State senate passes the transit funding bill after a long debate {'abcde'[i]}", f"https://us/{i}", 3)
    for i, k in enumerate(WORLD[:5]):
        put(c, k, f"Global shipping talks agree a new emissions levy for cargo ships {'abcde'[i]}", f"https://w/{i}", 3)
    top = titles(c, "top")
    assert top[0].startswith("Global shipping talks")
    s = {x["title"][:12]: x for x in c.stories.list("world", "", "top", 5)["stories"]}
    assert all(x["indep"] == 5 for x in s.values())


def test_a_new_development_of_an_old_storyline_ranks_by_its_own_recent_coverage(c):
    for i, k in enumerate(WORLD[:3]):
        put(c, k, f"Ceasefire talks open in Doha between the warring sides {'abc'[i]}", f"https://o1/{i}", 40)
    for i, k in enumerate(WORLD[3:6]):
        put(c, k, f"Ceasefire talks in Doha stall as warring sides trade blame {'abc'[i]}", f"https://o2/{i}", 3)
    rows = c.repo.q("SELECT thread_id FROM stories")
    assert len({r["thread_id"] for r in rows}) == 1 and len(rows) == 2                      # two developments of one thread
    assert [t[:29] for t in titles(c, "top")] == ["Ceasefire talks in Doha stall"]            # the earlier development has no report in the last day
    assert [t[:29] for t in titles(c, "top", "fresh")] == ["Ceasefire talks in Doha stall"]    # and the later one is a new event: fresh, though the storyline is old


def test_a_story_reported_hour_after_hour_stays_one_story_and_counts_the_last_day(c):
    # first report 29 hours ago, then a report every 5 hours: past the old 24 hours from the first report, still one story
    for i, (k, h) in enumerate(zip(WORLD[:7], (29, 24, 19, 14, 9, 4, 1))):
        put(c, k, f"Nvidia chief executive resigns as board accepts the departure {'abcdefg'[i]}", f"https://r/{i}", h)
    assert c.repo.one("SELECT count(*) AS n FROM stories")["n"] == 1
    row = c.stories.list("world", "", "top", 5)["stories"][0]
    assert row["outlets_period"] == 5 and row["bucket"] == "ongoing"                        # the five outlets of the last day; begun 29 hours ago, so ongoing, not fresh
    assert titles(c, "top", "fresh") == [] and len(titles(c, "top", "ongoing")) == 1


def test_a_pause_in_the_reports_ends_a_story(c):
    for i, k in enumerate(WORLD[:3]):
        put(c, k, f"Markets fall as investors wait for the central bank decision {'abc'[i]}", f"https://m1/{i}", 30)
    for i, k in enumerate(WORLD[3:6]):
        put(c, k, f"Markets fall again as investors wait for the central bank decision {'abc'[i]}", f"https://m2/{i}", 1)       # 29 hours of silence between
    assert c.repo.one("SELECT count(*) AS n FROM stories")["n"] == 2


def test_the_same_text_again_after_a_pause_is_still_a_reprint(c):
    put(c, "bbc", "Markets fall as investors wait for the central bank decision", "https://m3/1", 30, lead="Shares fell in Asia and Europe on Thursday.")
    put(c, "dw", "Markets fall as investors wait for the central bank decision", "https://m3/2", 1, lead="Shares fell in Asia and Europe on Thursday.")      # word for word, a day later
    assert c.repo.one("SELECT count(*) AS n FROM stories")["n"] == 1


def test_a_hot_topic_with_many_newer_stories_still_finds_its_story(c):
    put(c, "bbc", "Trump calls for Apple ex-CEO Tim Cook to replace Huang at Nvidia", "https://h/0", 0.6)
    for i in range(60):                                            # sixty newer stories that share a word with the report (a name in the news)
        w = lambda k: f"{chr(97 + i % 26)}{chr(97 + i // 26)}{chr(97 + k)}ium"                # four words of its own: no two of these stories are alike
        put(c, WORLD[i % 5], f"Huang {w(1)} {w(2)} {w(3)} {w(4)} announced", f"https://h/n{i}", 0.5 - i * 0.001)
    put(c, "dw", "Donald Trump calls for former Apple CEO Tim Cook to replace Jensen Huang at Nvidia", "https://h/1", 0.2)
    row = c.repo.one("SELECT outlets FROM stories WHERE title ILIKE '%Tim Cook%' ORDER BY outlets DESC LIMIT 1")
    assert row["outlets"] == 2


def test_a_date_without_a_time_is_the_time_it_was_seen():
    from app.services.ingest import effective_time
    now = NOW()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert effective_time(midnight, now) == now                                            # "12 Nov 2026" and no time
    assert effective_time(midnight - dt.timedelta(days=5), now) == midnight - dt.timedelta(days=5)      # an old date stays
    assert effective_time(now + dt.timedelta(hours=3), now) == now                        # the future is the time of the check
    t = now - dt.timedelta(hours=2, minutes=7)
    assert effective_time(t, now) == t


def test_trending_counts_only_the_last_trending_hours_whenever_the_story_began(c):
    for i, (k, h) in enumerate(zip(WORLD[:4], (20, 15, 10, 5))):                            # a story begun yesterday, reported every 5 hours
        put(c, k, f"Parliament debates the new data protection law in committee {'abcd'[i]}", f"https://b0/{i}", h)
    for i, k in enumerate(WORLD[4:10]):                                                    # and a burst in the last hour
        put(c, k, f"Parliament debates the new data protection law in committee {'efghij'[i]}", f"https://b2/{i}", 0.5)
    assert c.repo.one("SELECT count(*) AS n FROM stories")["n"] == 1
    row = c.stories.list("world", "", "trending", 5)["stories"][0]
    assert row["title"].startswith("Parliament debates") and row["outlets_period"] == 6        # the six of the last two hours, not all ten
    c.settings.set_many({"feed.trending_hours": 1})
    put(c, "bbc", "Quiet storyline: council budget consultation continues", "https://q/1", 30)
    put(c, "dw", "Quiet storyline: council budget consultation continues again", "https://q/2", 0.2)
    assert not any("Quiet storyline" in t for t in titles(c, "trending"))                  # one outlet in the window is not trending


def test_lists_show_english_only_by_default(c):
    for i, k in enumerate(("bbc", "dw", "france24")):
        put(c, k, "Floods close schools across the northern provinces today" + ["", " officials say", " as rivers rise"][i], f"https://l/{i}", 1 - i * 0.2)
    c.repo.run("UPDATE stories SET lang = 'fr'")
    assert titles(c, "top") == []
    c.settings.set_many({"feed.languages_shown": ["all"]})
    assert len(titles(c, "top")) == 1


def test_the_one_time_split_cuts_long_stories_into_developments(c):
    put(c, "bbc", "Storm Amy batters the west coast with record winds", "https://x/1", 60)
    sid = c.repo.one("SELECT id FROM stories")["id"]
    with c.repo.pool.connection() as conn:                       # an old-style story: a report two days later attached to it
        now = NOW()
        c.repo.ingest_article(conn, {"seen_day": now.date(), "source_key": "dw", "feed_id": None, "url": "https://x/2", "url_hash": "x2",
                                     "title": "Clean-up begins after Storm Amy", "lead": "", "published_at": now - dt.timedelta(hours=2), "seen_at": now,
                                     "country": "DE", "stype": "public_broadcaster"}, sid, None, {}, {})
    res = c.repo.split_developments(24, title_key)
    assert res["developments_added"] == 1
    rows = c.repo.q("SELECT id, thread_id, title FROM stories ORDER BY first_seen")
    assert [r["thread_id"] for r in rows] == [sid, sid] and rows[1]["title"].startswith("Clean-up begins")


def test_several_places_and_topics_in_one_scope(c):
    put(c, "bbc", "Floods close schools in Chennai as heavy rain continues in Tamil Nadu", "https://m/1", 2)
    put(c, "dw", "Bundestag passes new budget after long parliament debate in Berlin Germany", "https://m/2", 2)
    put(c, "cbc", "Toronto Maple Leafs beat Canadiens in NHL ice hockey overtime thriller", "https://m/3", 2)
    c.settings.set_many({"feed.min_outlets": 1})
    got = lambda place, topic="": {s["title"][:12] for s in c.stories.list(place, topic, "top", 20)["stories"]}
    assert got("IN-TN,DE") == {"Floods close", "Bundestag pa"}                          # any of the places
    assert got("world", "sport,politics") == {"Bundestag pa", "Toronto Mapl"}            # any of the topics
    assert got("IN-TN,DE", "politics") == {"Bundestag pa"}                              # places and topics together
    assert c.stories.check_scope("IN,world", "") == ("world", "")                        # Global already includes every place
    assert c.stories.scope_label("IN-TN,DE", "sport,politics") == "Sport, Politics and government · Tamil Nadu, Germany"
    with pytest.raises(Exception):
        c.stories.check_scope("IN,Narnia", "")
