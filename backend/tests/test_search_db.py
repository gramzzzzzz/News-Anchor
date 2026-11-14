import os
import datetime as dt

import pytest

from app.services.embedder import hashed_vector
from app.services.textutil import title_key
from tests.conftest import FakeFetcher

pytestmark = pytest.mark.db
UTC = dt.timezone.utc


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_merge_rule():
    """The calibrated pair rule (merge.py docstring): similarity bands, rare shared words, and the guards."""
    from app.services.merge import casualty_clash, decide, rare_overlap, routine
    anc = {"US-MA-salem": ["US-MA", "US"], "US-OR-salem": ["US-OR", "US"], "IN-TN-chennai": ["IN-TN", "IN"], "IN-TN": ["IN"]}
    A = lambda c: anc.get(c, [])
    x, y = "Binance hacked as thieves take $400 million in crypto", "Hackers steal $400 million in crypto from Binance exchange"
    assert decide(0.85, 0.0, x, y, set(), set(), A)                             # very similar: merge
    assert decide(0.70, 0.30, x, y, set(), set(), A)                            # similar and sharing rare words: merge
    assert not decide(0.70, 0.02, x, y, set(), set(), A)                        # similar wording, nothing distinctive shared
    assert not decide(0.60, 0.90, x, y, set(), set(), A)                        # below the floor
    assert not decide(0.95, 1.0, "Daily horoscope for Aries, Taurus and Gemini", "Daily horoscope for Leo, Virgo and Libra", set(), set(), A)
    assert routine("Police blotter: arrests this week") and not routine(x)
    a, b = "3 killed as bus overturns near Salem", "12 killed as bus overturns near Salem"
    assert casualty_clash(a, b) and not decide(0.95, 0.9, a, b, set(), set(), A)                 # different death tolls are different reports
    assert not casualty_clash("3 killed as bus overturns near Salem", "Bus overturns near Salem, 3 killed")
    a2, b2 = "Four killed, 88 injured as two trains collide in Egypt", "7 killed, 125 injured as two trains collide in Egypt"
    assert casualty_clash(a2, b2)
    assert decide(0.85, 0.9, a2, b2, {"EG-C"}, {"EG-C"}, A)                                     # a revised toll: alike, one place
    assert not decide(0.85, 0.9, a, b, set(), set(), A)                                         # no place known: still two events
    assert not decide(0.80, 0.9, a2, b2, {"EG-C"}, {"EG-C"}, A)                                 # not alike enough for a revised toll
    s1, s2 = "Shooting in Salem leaves one man injured overnight", "Overnight shooting in Salem injures one man"
    assert not decide(0.95, 0.9, s1, s2, {"US-MA-salem"}, {"US-OR-salem"}, A)                   # same words, two different towns
    assert decide(0.95, 0.9, s1, s2, {"US-MA-salem"}, {"US-MA-salem"}, A)
    assert decide(0.95, 0.9, "Rain floods Chennai streets again tonight", "Chennai streets flooded after heavy rain", {"IN-TN-chennai"}, {"IN-TN"}, A)   # a city within the state named
    assert rare_overlap({"binance", "hack"}, {"binance", "steal"}, {"binance": 3.0, "hack": 1.0, "steal": 1.0}) == 0.75


def test_hashed_vectors_group_rewordings():
    v = hashed_vector
    same = cos(v("Union cabinet clears harbour dredging contract worth crores"), v("Harbour dredging contract worth crores cleared by union cabinet"))
    other = cos(v("Union cabinet clears harbour dredging contract worth crores"), v("Chennai temple festival crowd plan approved"))
    assert same > 0.6 > other + 0.3 and abs(sum(x * x for x in v("anything at all")) - 1) < 1e-9


def make_story(c, source, title, url, lead="", hours_ago=1, topics=None):
    now = dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)
    with c.repo.pool.connection() as conn:
        a = {"seen_day": now.date(), "source_key": source, "feed_id": None, "url": url, "url_hash": url, "title": title, "lead": lead, "published_at": now,
             "seen_at": now, "country": "IN", "stype": "news"}
        return c.repo.ingest_article(conn, a, None, {"title": title, "lead": lead, "tokens": title_key(title)}, topics or {}, {})[0]


async def test_merger_folds_reworded_duplicates_and_keeps_different_events_apart(container):
    c = container
    c.settings.set_many({"search.embeddings": "hashed"})
    c.repo.ensure_partition(dt.date.today())
    s1 = make_story(c, "hindu", "Union cabinet clears harbour dredging contract worth Rs 4,200 crore", "https://x/1", "The Union cabinet on Wednesday cleared a harbour dredging contract.")
    s2 = make_story(c, "bbc", "Harbour dredging contract worth Rs 4,200 crore cleared by Union cabinet", "https://x/2", "Cabinet cleared harbour dredging.", hours_ago=0)
    s3 = make_story(c, "ndtv", "Verstappen wins in Brazil after late rain", "https://x/3")
    s4 = make_story(c, "ht", "Norris crashes out of Mexico qualifying in heavy rain", "https://x/4")
    s5 = make_story(c, "dtnext", "Chennai temple festival crowd plan approved", "https://x/5")
    assert len({s1, s2, s3, s4, s5}) == 5
    res = await c.merger.run()
    assert res["backend"] == "hashed" and res["embedded"] == 5 and res["merged"] == 1, res
    ids = {r["id"] for r in c.repo.q("SELECT id FROM stories")}
    assert ids == {s1, s3, s4, s5}                                           # the later duplicate folded into the earlier story
    st = c.repo.one("SELECT * FROM stories WHERE id = %s", (s1,))
    assert st["articles"] == 2 and st["outlets"] == 2 and st["embed_n"] == 2
    assert {r["source_key"] for r in c.repo.q("SELECT source_key FROM articles WHERE story_id = %s", (s1,))} == {"hindu", "bbc"}
    assert (await c.merger.run())["embedded"] == 0                          # nothing new, nothing redone
    # switching the backend clears and rebuilds the vectors
    c.settings.set_many({"search.embeddings": "off"})
    assert (await c.merger.run()) == {"backend": "off"}


async def test_search_finds_by_words_and_by_similarity(container):
    c = container
    c.settings.set_many({"search.embeddings": "hashed"})
    c.repo.ensure_partition(dt.date.today())
    a = make_story(c, "hindu", "Cyclone warning for Tamil Nadu coast as depression deepens", "https://s/1", "IMD issued an alert for fishermen.")
    b = make_story(c, "bbc", "Parliament passes new data protection amendment", "https://s/2", "The bill passed after a debate.")
    await c.merger.run()
    r = await c.search.search("cyclone")
    assert [s["id"] for s in r["stories"]][:1] == [a] and "«" in (r["stories"][0]["hit"] or "") and r["mode"].startswith("words")
    r = await c.search.search("cyclones warning fishermen")                  # stemming: cyclones -> cyclone
    assert r["stories"] and r["stories"][0]["id"] == a
    r = await c.search.search("data protection law", topic="")
    assert r["stories"][0]["id"] == b
    assert r["stories"][0]["verification"]["level"] in ("single", "caution", "confirmed", "reported")
    with pytest.raises(ValueError):
        await c.search.search("a")
    assert (await c.search.search("zzzzqqq"))["count"] == 0


def put_article(c, source, title, url, hours_ago, topics=(), places=()):
    now = dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)
    c.repo.ensure_partition(now.date())
    with c.repo.pool.connection() as conn:
        a = {"seen_day": now.date(), "source_key": source, "feed_id": None, "url": url, "url_hash": url, "title": title, "lead": "", "published_at": now,
             "seen_at": now, "country": "IN", "stype": "news"}
        c.repo.ingest_article(conn, a, None, {"title": title, "lead": "", "tokens": title_key(title)}, {t: 1.0 for t in topics}, {p: 1.0 for p in places})


async def test_explore_statistics_and_filters(container):
    c = container
    c.repo.ensure_partition(dt.date.today())
    c.repo.run("UPDATE sources SET band = 'reliable' WHERE key IN ('hindu', 'bbc')")
    c.repo.run("UPDATE sources SET band = 'low' WHERE key = 'ht'")
    for i, (src, h) in enumerate([("hindu", 2), ("hindu", 3), ("bbc", 3), ("ht", 5), ("ndtv", 30)]):
        put_article(c, src, f"Floods hit Chennai suburbs as lake overflows, report {i}", f"https://e/{i}", h, topics=("disaster", "disaster.storms-floods"), places=("IN", "IN-TN"))
    put_article(c, "bbc", "Parliament debates data bill", "https://e/other", 4, topics=("politics",), places=("IN",))
    d = await c.search.explore({"q": "floods", "days": 2})
    assert d["totals"]["articles"] == 5 and d["totals"]["sources"] == 4 and d["totals"]["stories"] >= 1
    assert sum(p["n"] for p in d["timeline"]) == 5 and d["filters"]["bucket"] == "hour" and len(d["timeline"]) >= 47      # empty hours are zeros, not gaps
    assert d["sources"][0]["key"] == "hindu" and d["sources"][0]["n"] == 2
    assert {b["band"]: b["n"] for b in d["bands"]}["reliable"] == 3 and d["share_reliable"] == 0.6
    assert any(t["code"] == "disaster.storms-floods" for t in d["topics"]) and any(p["code"] == "IN-TN" for p in d["places"])
    assert any("floods" not in t["term"] for t in d["terms"]) and all("floods" != t["term"] for t in d["terms"])           # the query word is not its own top term
    assert len(d["articles"]) == 5 and d["articles"][0]["source_key"] in ("hindu", "bbc")
    only_low = await c.search.explore({"q": "floods", "days": 2, "bands": ["low"]})
    assert only_low["totals"]["articles"] == 1 and only_low["articles"][0]["source_key"] == "ht"
    topic = await c.search.explore({"days": 2, "topic": "politics"})
    assert topic["totals"]["articles"] == 1
    narrow = await c.search.explore({"q": "floods", "days": 30, "sources": ["ndtv"]})
    assert narrow["totals"]["articles"] == 1 and narrow["filters"]["bucket"] == "day"
    with pytest.raises(ValueError):
        await c.search.explore({"place": "nowhere"})
    with pytest.raises(ValueError):
        await c.search.explore({"since": "2026-02-01T00:00:00", "until": "2026-01-01T00:00:00"})
    cmp = await c.search.compare(["floods", "data bill"], {"days": 2})
    assert [s["q"] for s in cmp["series"]] == ["floods", "data bill"] and cmp["series"][0]["total"] == 5 and cmp["series"][1]["total"] == 1
    with pytest.raises(ValueError):
        await c.search.compare(["only one"], {})


def test_explore_and_search_api(client, container):
    c = container
    c.repo.ensure_partition(dt.date.today())
    put_article(c, "hindu", "Metro extension tender draws three bidders", "https://a/1", 2, topics=("economy.transport",), places=("IN",))
    H = {"X-Requested-With": "news-anchor"}
    assert client.post("/api/explore", json={"q": "metro"}, headers=H).status_code == 401
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    r = client.post("/api/explore", json={"q": "metro", "days": 3}, headers=H)
    assert r.status_code == 200 and r.json()["totals"]["articles"] == 1
    assert client.post("/api/explore", json={"place": "xx"}, headers=H).status_code == 422
    assert client.get("/api/search?q=metro", headers=H).json()["count"] == 1
    assert client.get("/api/search?q=m", headers=H).status_code == 422
    assert client.get("/api/search/status").json()["stories"] == 1


async def test_changing_method_rebuilds_vectors_and_never_compares_across_methods(container):
    c = container
    c.settings.set_many({"search.embeddings": "hashed"})
    c.repo.ensure_partition(dt.date.today())
    a = make_story(c, "hindu", "Cyclone warning for Tamil Nadu coast as depression deepens", "https://m/1", "IMD alert.")
    await c.merger.run()
    assert c.repo.one("SELECT embed_backend FROM stories WHERE id = %s", (a,))["embed_backend"] == "hashed"
    calls = {"n": 0}

    async def fake_backend():
        return "model"

    async def fake_embed(texts):
        calls["n"] += len(texts)
        return [hashed_vector(t) for t in texts], "model"

    c.embedder.backend, c.embedder.embed = fake_backend, fake_embed
    res = await c.merger.run()
    assert res["embedded"] == 1 and res["backend"] == "model" and calls["n"] == 1                       # rebuilt with the new method
    assert c.repo.one("SELECT embed_backend FROM stories WHERE id = %s", (a,))["embed_backend"] == "model"
    assert (await c.merger.run())["embedded"] == 0
    assert c.repo.embedded_count("hashed")["embedded"] == 0 and c.repo.embedded_count("model")["embedded"] == 1
    assert c.repo.search_semantic(hashed_vector("cyclone"), "hashed") == [] and len(c.repo.search_semantic(hashed_vector("cyclone warning"), "model")) == 1
