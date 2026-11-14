from app.services.ingest import match_story, parse_sources_file
from app.services.settings_service import SOURCE_TYPES
from app.services.topics import Taxonomy


def test_starter_registry_is_consistent():
    sources, feeds = parse_sources_file()
    tax = Taxonomy.load()
    keys = {s["key"] for s in sources}
    assert len(keys) == len(sources) and len(feeds) > 150
    assert all(s["type"] in SOURCE_TYPES and s["scope"] in ("national", "international") and len(s["country"]) == 2 for s in sources)
    assert all(f["source_key"] in keys for f in feeds)
    assert all(t in tax.topics for f in feeds for t in f["topics"])
    assert len({f["url"] for f in feeds}) == len(feeds), "duplicate feed URL"
    assert all(f["url"].startswith("https://") for f in feeds)
    assert {s["key"] for s in sources} <= {f["source_key"] for f in feeds}, "source without any feed"


def test_f1_has_dedicated_sources():
    sources, feeds = parse_sources_file()
    names = {s["key"] for s in sources}
    f1 = {f["source_key"] for f in feeds if "sport.motor-racing" in f["topics"]}
    assert {"f1", "autosport", "motorsport", "racefans", "crash", "therace"} & f1 and "f1" in names and len(f1) >= 6


def test_match_story():
    cands = [{"id": 1, "tokens": ["india", "bloc", "postpones", "public", "impeachment", "cec", "rain"]},
             {"id": 2, "tokens": ["google", "value", "plunges", "500bn", "search", "ads"]}]
    assert match_story(["india", "bloc", "postpones", "impeachment", "cec", "october", "rain"], cands) == 1
    assert match_story(["binance", "hack", "6", "3bn"], cands) is None
    # same words, different place: generic overlap must not merge two unrelated stories
    philippines = [{"id": 4, "tokens": ["senate", "race", "philippine", "midterm", "candidate", "control", "vote", "manila", "survey", "lead", "poll"]}]
    assert match_story(["race", "decide", "control", "senate"], philippines) is None
    # a daily series with a different date is a different story
    assert match_story(["gold", "rate", "today", "october", "30", "chennai", "mumbai"], [{"id": 3, "tokens": ["gold", "rate", "today", "october", "29", "delhi", "kolkata"]}]) is None


def test_feed_text_cleanup():
    from app.dao.feeds.rss import parse_feed
    body = b'<rss version="2.0"><channel><item><title>Alonso hits back</title><link>https://x.example/a</link><description>He said the criticism was fair ... Keep reading</description></item><item><title>Second</title><link>https://x.example/b</link><description>Body text. The post Second appeared first on Some Site.</description></item></channel></rss>'
    items, recognised = parse_feed(body)
    assert recognised and items[0]["lead"] == "He said the criticism was fair" and items[1]["lead"] == "Body text."
    assert parse_feed(b'<rss version="2.0"><channel></channel></rss>') == ([], True)
    assert parse_feed(b"not a feed")[1] is False
