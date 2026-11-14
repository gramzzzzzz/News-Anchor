import os
import datetime as dt
import io
import json
import zipfile

import httpx
import pytest

from app.dao.discovery import wikidata_feeds
from tests.conftest import FakeFetcher, rss

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)


def recent_feed(prefix: str, n: int = 5) -> bytes:
    return rss([{"title": f"{prefix} headline number {i} about the council and the harbour", "link": f"https://{prefix}.example/a{i}", "desc": "the council said",
                 "pub": NOW - dt.timedelta(hours=i + 1)} for i in range(n)])


class FakeMC:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True

    async def national_collections(self, include_local=False):
        return [{"id": 1, "name": "India - National", "source_count": 5}]

    async def collection_sources(self, cid):
        m = dt.date.today().strftime("%m/%Y")
        base = {"last_story": m, "monitored": True, "media_type": "digital_native", "pub_country": "IND"}
        return [
            {**base, "id": 11, "name": "anews.example", "label": "A News", "homepage": "https://anews.example/", "primary_language": "en", "stories_per_week": 500},
            {**base, "id": 12, "name": "hindi.example", "label": "Hindi Samachar", "homepage": "https://hindi.example/", "primary_language": "hi", "stories_per_week": 900},
            {**base, "id": 13, "name": "tiny.example", "label": "Tiny", "homepage": "https://tiny.example/", "primary_language": "en", "stories_per_week": 1},
            {**base, "id": 14, "name": "thehindu.com", "label": "The Hindu again", "homepage": "https://www.thehindu.com/", "primary_language": "en", "stories_per_week": 800},
            {**base, "id": 15, "name": "mha.gov.in", "label": "Home Ministry", "homepage": "https://mha.gov.in/", "primary_language": "en", "stories_per_week": 300},
            {**base, "id": 16, "name": "stale.example", "label": "Stale", "homepage": "https://stale.example/", "primary_language": "en", "stories_per_week": 80, "last_story": "01/2020"},
            {**base, "id": 18, "name": "lostcrawl.example", "label": "Lost Crawl", "homepage": "https://lostcrawl.example/", "primary_language": "en", "stories_per_week": 0, "collection_count": 9},
            {**base, "id": 17, "name": "mcfeed.example", "label": "MC Feed Only", "homepage": "https://mcfeed.example/", "primary_language": "en", "stories_per_week": 60},
        ]

    async def source_feeds(self, sid):
        return [{"id": 1, "url": "https://mcfeed.example/hidden/rss", "admin_rss_enabled": True}] if sid == 17 else []


WIKIDATA = {"results": {"bindings": [
    {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "itemLabel": {"value": "Wiki Daily"}, "site": {"value": "https://wikidaily.example/"},
     "feed": {"value": "https://wikidaily.example/rss"}, "cc": {"value": "KE"}, "lang": {"value": "en"}},
    {"item": {"value": "http://www.wikidata.org/entity/Q2"}, "itemLabel": {"value": "Journal Polonais"}, "site": {"value": "https://polish.example/"},
     "feed": {"value": "https://polish.example/rss"}, "cc": {"value": "PL"}, "lang": {"value": "pl"}}]}}


@pytest.fixture
def disc(container):
    c = container
    page = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body>hello</body></html>'
    c.discovery.fetcher = FakeFetcher({
        "https://anews.example/": page.encode(), "https://anews.example/feed.xml": recent_feed("anews"),
        "https://wikidaily.example/rss": recent_feed("wikidaily"),
        "https://mcfeed.example/": b"<html>no links</html>", "https://mcfeed.example/hidden/rss": recent_feed("mcfeed"),
        "https://mha.gov.in/": b"<html>no feed here</html>",
    })
    c.discovery.mc_factory = lambda token: FakeMC()
    c.discovery.token_fallback = "token"
    c.discovery.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=json.dumps(WIKIDATA))))
    c.settings.set_many({"search.embeddings": "hashed", "sources.all_languages": False})      # these tests are about the language filter
    return c


def by_domain(c, d):
    return c.repo.one("SELECT * FROM sources WHERE domain = %s", (d,))


async def test_directory_adds_only_wanted_sources_once(disc):
    c = disc
    before = c.repo.one("SELECT count(*) AS n FROM sources")["n"]
    res = await c.discovery.run_directory()
    assert res["added"] == {"mediacloud": 4, "wikidata": 1}, res           # A News, Home Ministry, MC Feed Only, Lost Crawl; Wiki Daily. Not: Hindi, Tiny, The Hindu (known), Stale, Polish
    assert by_domain(c, "lostcrawl.example") is not None                    # Media Cloud counting 0 a week (its crawl lost the site, as with rt.com) does not exclude it
    assert c.repo.one("SELECT count(*) AS n FROM sources")["n"] == before + 5
    a = by_domain(c, "anews.example")
    assert (a["status"], a["origin"], a["country"], a["language"], a["curated"], a["type"]) == ("candidate", "mediacloud", "IN", "en", False, "news")
    assert by_domain(c, "mha.gov.in")["type"] == "official"
    assert by_domain(c, "wikidaily.example")["country"] == "KE" and by_domain(c, "polish.example") is None
    assert by_domain(c, "hindi.example") is None and by_domain(c, "tiny.example") is None and by_domain(c, "stale.example") is None
    assert [f["url"] for f in c.repo.pending_feeds(by_domain(c, "wikidaily.example")["key"])] == ["https://wikidaily.example/rss"]
    again = await c.discovery.run_directory()                                # idempotent
    assert again["added"] == {"mediacloud": 0, "wikidata": 0}


async def test_feed_search_validates_and_promotes(disc):
    c = disc
    await c.discovery.run_directory()
    res = await c.discovery.run_batch(50)
    assert res["searched"] == 5 and res["found"] == 3 and res["none"] == 2, res
    a, w, m, p = (by_domain(c, d) for d in ("anews.example", "wikidaily.example", "mcfeed.example", "mha.gov.in"))
    assert a["status"] == "active" and w["status"] == "active" and m["status"] == "active"           # advertised link / Wikidata feed / Media Cloud's list
    assert p["status"] == "no_feed" and "No usable feed" in p["status_note"]
    assert c.repo.source_feeds(a["key"])[0]["url"] == "https://anews.example/feed.xml"
    assert c.repo.one("SELECT count(*) AS n FROM feeds WHERE state = 'pending'")["n"] == 0
    assert c.repo.kv_get("trust_dirty") is True                               # new sources are rated soon
    c.repo.run("UPDATE feeds SET next_poll_at = now()")
    due = {f["source_key"] for f in c.repo.feeds_due(["en"])}
    assert a["key"] in due and w["key"] in due and p["key"] not in due
    # a candidate or feedless source is never polled; neither is one in a language that is not enabled
    assert not c.repo.feeds_due(["ta"])


async def test_recent_items_and_language_are_checked_before_a_feed_is_accepted(disc):
    c = disc
    old = rss([{"title": f"Old headline {i} about something long past", "link": f"https://old.example/{i}", "pub": NOW - dt.timedelta(days=90)} for i in range(5)])
    c.discovery.fetcher.feeds["https://old.example/rss"] = old
    from app.dao.discovery import feedfind
    assert await feedfind.validate(c.discovery.fetcher, "https://old.example/rss") is None                  # stale
    assert await feedfind.validate(c.discovery.fetcher, "https://anews.example/feed.xml") is not None
    thin = rss([{"title": "Just one", "link": "https://thin.example/1", "pub": NOW}])
    c.discovery.fetcher.feeds["https://thin.example/rss"] = thin
    assert await feedfind.validate(c.discovery.fetcher, "https://thin.example/rss") is None                 # too few items
    from app.services.discovery import looks_english
    assert looks_english(["Council approves the new harbour plan after protest", "Minister says talks will continue"])
    assert not looks_english(["Rada miejska zatwierdziła nowy plan portu", "Minister powiedział, że rozmowy będą kontynuowane"])


async def test_lifecycle_moves_sources_through_degraded_dormant_retired_and_back(disc):
    c = disc
    await c.discovery.run_directory()
    await c.discovery.run_batch(50)
    a = by_domain(c, "anews.example")
    assert c.repo.lifecycle_update() == {"retired": 0}                         # healthy feeds change nothing
    c.repo.run("UPDATE feeds SET state = 'degraded', fail_count = 6 WHERE source_key = %s", (a["key"],))
    assert c.repo.lifecycle_update().get("degraded") == 1 and by_domain(c, "anews.example")["status"] == "degraded"
    c.repo.run("UPDATE feeds SET state = 'dormant', fail_count = 25 WHERE source_key = %s", (a["key"],))
    assert c.repo.lifecycle_update().get("dormant") == 1 and by_domain(c, "anews.example")["status"] == "dormant"
    c.repo.run("UPDATE feeds SET next_poll_at = now()")
    assert any(f["source_key"] == a["key"] for f in c.repo.feeds_due(["en"]))           # dormant feeds are still tried now and then
    c.repo.run("UPDATE sources SET status_changed_at = now() - interval '100 days' WHERE key = %s", (a["key"],))
    assert c.repo.lifecycle_update().get("retired") == 1
    assert by_domain(c, "anews.example")["status"] == "retired" and c.repo.one("SELECT state FROM feeds WHERE source_key = %s", (a["key"],))["state"] == "retired"
    assert not any(f["source_key"] == a["key"] for f in c.repo.feeds_due(["en"]))
    hist = [h["change"] for h in c.repo.source_history(a["key"])]
    assert "dormant to retired" in hist and "active to degraded" in hist
    assert c.repo.source_recheck(a["key"])                                     # the owner can ask for another look
    assert by_domain(c, "anews.example")["status"] == "candidate"
    assert not c.repo.source_recheck("hindu")                                  # hand-picked sources are not re-discovered


async def test_curated_sources_are_never_retired_by_the_lifecycle(disc):
    c = disc
    c.repo.run("UPDATE feeds SET state = 'dormant', fail_count = 30 WHERE source_key = 'hindu'")
    c.repo.run("UPDATE sources SET status = 'dormant', status_changed_at = now() - interval '200 days' WHERE key = 'hindu'")
    c.repo.lifecycle_update()
    assert c.repo.one("SELECT status FROM sources WHERE key = 'hindu'")["status"] == "dormant"
    c.repo.run("UPDATE feeds SET next_poll_at = now()")
    assert any(f["source_key"] == "hindu" for f in c.repo.feeds_due(["en"]))


def test_sources_page_filters_and_paging(client, container):
    c = container
    c.repo.candidate_add({"key": "zz-test", "name": "ZZ Test", "domain": "zz.example", "country": "NG", "type": "news", "language": "en", "origin": "mediacloud", "mc_id": 99,
                          "homepage": "https://zz.example/", "media_type": None, "stories_per_week": 7.0, "note": "t"})
    H = {"X-Requested-With": "news-anchor"}
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    d = client.get("/api/sources?status=candidate&facets=true").json()
    assert d["total"] == 1 and d["sources"][0]["key"] == "zz-test" and d["facets"]["status"]["candidate"] == 1 and d["facets"]["status"]["active"] >= 129
    assert client.get("/api/sources?q=zz.exa").json()["total"] == 1
    page = client.get("/api/sources?limit=10&offset=10&sort=name").json()
    assert len(page["sources"]) == 10 and page["total"] >= 130
    assert client.get("/api/discovery").json()["counts"]["candidate"] == 1


async def test_ceiling_on_discovered_sources_and_local_cap(disc):
    c = disc
    c.settings.set_many({"discovery.max_total": 100})
    res = await c.discovery.run_directory()
    assert sum(res["added"].values()) <= 5 and not res["notes"]
    c.repo.run("DELETE FROM sources WHERE curated = false")
    c.settings.set_many({"discovery.max_total": 100})
    c.repo.run("UPDATE sources SET curated = false WHERE key IN (SELECT key FROM sources ORDER BY key LIMIT 100)")       # pretend 100 are already discovered
    res = await c.discovery.run_directory()
    assert res["added"]["mediacloud"] == 0 and any("ceiling" in n for n in res["notes"])


def test_numbers_guard_ignores_decimals_and_years():
    from app.services.merge import numbers
    assert numbers("Growth 11.5% in 2026 and 3 deaths") == {"11.5", "3"}


# ---- searching the web -------------------------------------------------------------------------
GN_ROBOTS = """User-agent: *
Disallow: /
Allow: /$
Allow: /home$
Allow: /home?
Allow: /topics/
Allow: /stories/

User-agent: ClaudeBot
Disallow: /
"""


_RECENT = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(hours=2, minutes=13)      # recent, so the stored items are not "older than 72 hours"


def gn_card(name, url, title, when=_RECENT.strftime("%Y-%m-%dT%H:%M:%SZ")):
    """One story card as Google News draws it: the publisher's address sits in the card's base64 tracking data."""
    import base64
    data = base64.b64encode(json.dumps([None] * 8 + [url]).encode()).decode()
    return (f'<article><a class="WwrzSb" jsname="hXwDdf" jslog="95014; 5:{data}; track:click,vis" href="./read/CBMixyz?hl=en"></a>'
            f'<div class="vr1PYe">{name}</div><a class="gPFEn" href="./read/CBMixyz">{title}</a><time class="hvbAAd" datetime="{when}">1 hour ago</time></article>')


def gn_page(cards=(), menu=None):
    nav = "".join(f'<a class="brSCsc" href="./topics/{tid}?hl=en-US&amp;gl=US&amp;ceid=US%3Aen">{label}</a>' for label, tid in (menu or []))
    return f"<html><body><nav>{nav}</nav><main>{''.join(cards)}</main></body></html>"


def gn_rss(items):
    """A Google News RSS page. items: (publisher name, publisher site, headline, google id)."""
    body = "".join(f'<item><title>{t} - {n}</title><link>https://news.google.com/rss/articles/{g}?oc=5</link><pubDate>{_RECENT:%a, %d %b %Y %H:%M:%S} GMT</pubDate>'
                   f'<source url="{u}">{n}</source></item>' for n, u, t, g in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>q</title>{body}</channel></rss>'


def gn_decoder(request, links):
    """Google's two steps to the publisher's address: the link's page (signature, time), then the decode call."""
    if request.url.path.startswith("/rss/articles/"):
        gid = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, text=f'<html><c-wiz><div jscontroller="x" data-n-a-id="{gid}" data-n-a-ts="1730000000" data-n-a-sg="SIG{gid}"></div></c-wiz></html>')
    if request.url.path.endswith("/batchexecute"):
        from urllib.parse import parse_qs
        req = json.loads(parse_qs(request.content.decode())["f.req"][0])
        gid = json.loads(req[0][0][1])[2]
        if gid not in links:
            return httpx.Response(200, text=")]}'\n\n[]")
        inner = json.dumps(["garturlres", links[gid] + "?utm_source=gnews&gaa_at=la", 1])
        return httpx.Response(200, text=")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"], ["di", 42]]))
    return None


@pytest.fixture
def web(container):
    c = container
    c.settings.set_many({"search.embeddings": "hashed", "discovery.use_gdelt": True, "discovery.web_queries": 4})
    c.discovery.web_sleep = 0
    c.discovery.decode_sleep = 0
    c.discovery.calls = []
    state = {"gdelt_limited": False, "google_limited": False}
    links = {}

    def handler(request: httpx.Request) -> httpx.Response:
        c.discovery.calls.append(str(request.url))
        if request.url.host == "api.gdeltproject.org":
            if state["gdelt_limited"]:
                return httpx.Response(200, text="Please limit requests to one every 5 seconds or contact us")
            q = request.url.params.get("query", "")
            arts = [{"domain": d, "sourcecountry": "", "language": "English", "url": f"https://{d}/a", "title": "x"}
                    for d in ("www.harbourgazette.example", "harbourgazette.example", "thehindu.com", "msn.com", f"once-{abs(hash(q)) % 10000}.example")]
            return httpx.Response(200, json={"articles": arts})
        if request.url.host == "news.google.com":
            dec = gn_decoder(request, links)
            if dec is not None:
                return dec
            if state["google_limited"]:
                return httpx.Response(429)
            q = request.url.params.get("q", "")
            k = abs(hash(q)) % 10000
            links[f"H{k}"] = f"https://www.thehindu.com/news/national/search-story-{k}/article{k}.ece"
            return httpx.Response(200, text=gn_rss([("Harbour Gazette", "https://www.harbourgazette.example", f"Harbour story {k}", f"G{k}"),
                                                    ("Harbour Gazette", "https://www.harbourgazette.example", f"Second harbour story {k}", f"G2{k}"),
                                                    ("The Hindu", "https://www.thehindu.com", f"Council approves the new water supply plan for district {k} after long delays", f"H{k}"),
                                                    ("MSN", "https://www.msn.com", "Syndicated", f"M{k}"), ("Only Once", f"https://once-{k}.example", "Once", f"O{k}")]))
        return httpx.Response(404)

    c.discovery.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.web_state = state
    return c


def test_google_news_pages_and_gdelt_parsers():
    from app.dao.discovery import websearch as w
    ua = "NewsAnchor-test/0"
    assert w.robots_allows(GN_ROBOTS, w.home_url("en-IN"), ua) and w.robots_allows(GN_ROBOTS, w.topic_url("en-IN", "CAAq"), ua)
    for bad in ("https://news.google.com/rss?hl=en", "https://news.google.com/rss/search?q=x", "https://news.google.com/search?q=x", "https://news.google.com/read/CBM"):
        assert not w.robots_allows(GN_ROBOTS, bad, ua), bad                      # the longest rule wins: "Disallow: /" covers these
    assert not w.robots_allows(GN_ROBOTS, w.home_url("en-IN"), "Mozilla/5.0 (compatible; ClaudeBot/1.0)")   # a group naming the agent applies to it
    assert w.home_url("hi-IN").endswith("hl=hi&gl=IN&ceid=IN:hi") and w.home_url("en-IN").endswith("hl=en-IN&gl=IN&ceid=IN:en")
    menu = [("Top stories", "T1"), ("Local news", "L1"), ("India", "N1"), ("World", "W1"), ("Local", "L1"), ("Business", "B1"), ("Sports", "S1")]
    assert w.parse_sections(gn_page(menu=menu)) == {"TOP": "T1", "NATION": "N1", "WORLD": "W1", "BUSINESS": "B1", "SPORTS": "S1"}
    assert w.parse_sections(gn_page(menu=[("Accueil", "A"), ("Monde", "M")])) == {}
    cards = w.parse_cards(gn_page([gn_card("The Hindu", "https://www.thehindu.com/a.ece", "Rally &amp; march"), '<a class="WwrzSb" jslog="95014; 5:bm90IGpzb24"></a>']))
    assert cards == [{"url": "https://www.thehindu.com/a.ece", "name": "The Hindu", "title": "Rally & march", "published": _RECENT}]
    with pytest.raises(w.WebRateLimited):
        w.parse_gdelt("Please limit requests to one every 5 seconds")
    assert w.parse_gdelt('{"articles": [{"domain": "www.X.com", "sourcecountry": "India", "language": "English"}]}')[0]["domain"] == "x.com"
    assert w.parse_gdelt("") == []
    from app.services.discovery import site_of
    assert site_of("https://m.thewire.in/x") == "thewire.in" and site_of("https://www.bbc.co.uk/n") == "bbc.co.uk" and site_of("https://m.com/") == "m.com"


async def test_web_search_finds_publishers_after_repeat_sightings_and_skips_known_and_aggregators(web):
    c = web
    plan = c.discovery.plan_queries(6, {})
    assert len(plan) == 6 and len({p["key"] for p in plan}) == 6 and all(p["gl"] and len(p["gl"]) == 2 for p in plan)
    r1 = await c.discovery.run_web()
    assert r1["queries"] == 4 and r1["promoted"] == 1, r1                       # harbourgazette (seen in 4 searches); not: The Hindu (known), MSN (aggregator), the once-only sites
    hg = by_domain(c, "harbourgazette.example")
    assert (hg["country"], hg["scope"]) == ("XX", "international")               # the search edition says where readers are, not where the publisher is
    assert hg["status"] == "candidate" and hg["origin"] == "web" and "Google News pages or news searches" in hg["status_note"]
    assert by_domain(c, "msn.com") is None
    assert any("/rss/search?q=" in u for u in c.discovery.calls) and not any("api.gdeltproject.org" in u for u in c.discovery.calls)
    assert sum(1 for s in c.repo.q("SELECT 1 FROM sources WHERE domain LIKE 'once-%'")) == 0
    # the results from a publisher already collected (The Hindu) are stored as its articles, with its own address
    arts = c.repo.q("SELECT url, title FROM articles WHERE source_key = 'hindu'")
    assert r1["articles_new"] == len(arts) == 4 and all(a["url"].startswith("https://www.thehindu.com/news/national/search-story-") and "utm_" not in a["url"] and "gaa_" not in a["url"] for a in arts)
    stats = c.repo.web_stats()
    assert stats["seen"] >= 3 and stats["promoted"] >= 1
    done1 = set(c.repo.kv_get("web_done"))
    r2 = await c.discovery.run_web()
    assert r2["queries"] == 4 and r2["promoted"] == 0                           # nothing added twice
    assert len(set(c.repo.kv_get("web_done")) - done1) == 4                     # four different questions: the earlier ones are skipped for 14 days
    c.web_state["google_limited"] = True
    r3 = await c.discovery.run_web()
    assert r3["queries"] == 0 and "Stopped early" in r3["notes"][0]


async def test_web_search_with_gdelt(web):
    c = web
    c.settings.set_many({"discovery.web_engine": "gdelt"})
    r1 = await c.discovery.run_web()
    assert r1["queries"] == 4 and r1["promoted"] == 1, r1
    assert c.discovery.calls and all("api.gdeltproject.org" in u for u in c.discovery.calls)
    c.web_state["gdelt_limited"] = True
    r3 = await c.discovery.run_web()
    assert r3["queries"] == 0 and "Stopped early" in r3["notes"][0]


async def test_web_search_targets_the_thinnest_coverage_and_respects_limits(web):
    c = web
    c.repo.ensure_partition(dt.date.today())
    # give 'sports' lots of stories; 'space' none -> space is searched before sports
    from tests.test_search_db import make_story
    for i in range(5):
        make_story(c, "hindu", f"Cricket story number {i} about the squad selection", f"https://w/{i}", topics={"sport": 1.0})
    plan = c.discovery.plan_queries(40, {})
    topic_labels = [p["label"] for p in plan if p["label"] in ("Sports", "Space and astronomy")]
    assert topic_labels.index("Space and astronomy") < topic_labels.index("Sports") if "Sports" in topic_labels else True
    # a ceiling stops adding
    c.settings.set_many({"discovery.max_total": 100})
    c.repo.run("UPDATE sources SET curated = false WHERE key IN (SELECT key FROM sources ORDER BY key LIMIT 100)")
    assert c.discovery._room_left() <= 0 and "web" not in c.discovery.due_all()
    c.settings.reset(); 
    c.settings.set_many({"discovery.use_web": False})
    assert "web" not in c.discovery.due_all()


def test_web_discovery_api(client, container):
    H = {"X-Requested-With": "news-anchor"}
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    d = client.get("/api/discovery").json()
    assert d["web"]["seen"] == 0 and d["web_running"] is False and d["last_web"] is None


def test_country_from_domain():
    from app.services.discovery import country_from_domain
    v = {"GB", "IN", "DE", "KE"}
    assert country_from_domain("bbc.co.uk", v) == "GB" and country_from_domain("thehindu.in", v) == "IN" and country_from_domain("nation.co.ke", v) == "KE"
    assert country_from_domain("reuters.com", v) == "" and country_from_domain("pitchfork.tv", v) == "" and country_from_domain("x.zz", v) == ""


# ---- discovery v2: GDELT stream, Google News editions, Wikidata per country, balance, resumable directory -----------------------
def _zip(rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.CSV", "\n".join("\t".join(r) for r in rows))
    return buf.getvalue()


def _mention(eid: str, domain: str, url: str, lang: str = "") -> list[str]:
    r = [eid, "", "", "1", domain, url] + [""] * 9
    r[14] = f"srclc:{lang};eng:GT-{lang.upper()}" if lang else ""
    return r


def _event(eid: str, fips: str) -> list[str]:
    r = [""] * 61
    r[0], r[53] = eid, fips
    return r


@pytest.fixture
def stream(container):
    c = container
    c.discovery.calls = []
    first = {"stamp": None}

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        c.discovery.calls.append(u)
        stamp = u.rsplit("/", 1)[-1][:14]
        first["stamp"] = first["stamp"] or stamp
        if ".translation." in u:
            if u.endswith("mentions.CSV.zip"):
                return httpx.Response(200, content=_zip([_mention("9", "berlinpost.example", f"https://berlinpost.example/{stamp}/{i}", "deu") for i in range(2)]))
            return httpx.Response(200, content=_zip([_event("9", "GM")]))
        if u.endswith("mentions.CSV.zip"):
            rows = [_mention("1", "nairobi-daily.com", f"https://nairobi-daily.com/{stamp}/{i}") for i in range(2)]
            rows += [_mention(str(2 + k), "www.wireworld.example", f"https://wireworld.example/{stamp}/{k}") for k in range(3)]
            if stamp == first["stamp"]:
                rows.append(_mention("1", "once.example", "https://once.example/a"))
            return httpx.Response(200, content=_zip(rows))
        return httpx.Response(200, content=_zip([_event("1", "KE"), _event("2", "US"), _event("3", "UK"), _event("4", "IN")]))

    c.discovery.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.settings.set_many({"search.embeddings": "hashed", "discovery.gdelt_files": 4, "sources.all_languages": True})
    return c


async def test_gdelt_stream_counts_publishers_across_quarter_hours_and_finds_their_country_and_language(stream):
    c = stream
    r = await c.discovery.run_stream()
    assert r["files"] == 8 and r["missing"] == 0 and r["publishers"] == 4, r
    assert r["promoted"] == 3, r                                                 # nairobi-daily, berlinpost, wireworld; not once.example (one quarter hour)
    nd, bp, ww = by_domain(c, "nairobi-daily.com"), by_domain(c, "berlinpost.example"), by_domain(c, "wireworld.example")
    assert (nd["country"], nd["language"], nd["status"], nd["origin"]) == ("KE", "en", "candidate", "web")
    assert "GDELT" in nd["status_note"] and "quarter hours" in nd["status_note"]
    assert (bp["country"], bp["language"]) == ("DE", "de")                       # translated stream: language from GDELT, country from where events happen
    assert ww["country"] == "XX"                                                 # reports on several countries: not pinned to one
    assert by_domain(c, "once.example") is None
    n = len(c.discovery.calls)
    again = await c.discovery.run_stream()                                       # files already read are not read again
    assert again["files"] == 0 and len(c.discovery.calls) == n


async def test_gdelt_stream_respects_the_language_limit(stream):
    c = stream
    c.settings.set_many({"sources.all_languages": False, "sources.languages": ["en"]})
    await c.discovery.run_stream()
    assert by_domain(c, "berlinpost.example") is None and by_domain(c, "nairobi-daily.com") is not None
    assert c.repo.one("SELECT promoted FROM web_hits WHERE domain = 'berlinpost.example'")["promoted"] is False     # left waiting, in case German is turned on


@pytest.fixture
def editions(container):
    from app.dao.discovery import websearch
    c = container
    c.discovery.page_sleep = 0
    c.discovery.calls = []
    names = {"KE": "Kenya", "US": "U.S.", "GB": "U.K."}

    def handler(request: httpx.Request) -> httpx.Response:
        u = request.url
        c.discovery.calls.append(str(u))
        gl = u.params.get("gl", "")
        if u.path == "/robots.txt":
            return httpx.Response(200, text=GN_ROBOTS)
        if u.path == "/home":
            if gl == "ZW":
                return httpx.Response(404)
            menu = [("Top stories", f"TOP-{gl}"), ("Local news", "LOC"), (names.get(gl, f"Country {gl}"), f"NAT-{gl}"), ("World", f"WLD-{gl}"), ("Business", f"BUS-{gl}")]
            return httpx.Response(200, text=gn_page(menu=menu))
        if u.path == f"/topics/NAT-{gl}":
            cards = [gn_card("Reuters", f"https://www.reuters.com/world/{gl}", "Reuters story"), gn_card("Multi Post", f"https://multipost.example/{gl}", "Multi story"),
                     gn_card("The Hindu", f"https://www.thehindu.com/news/international/rally-in-{gl.lower()}/article1.ece", f"Opposition leaders hold a large rally in the capital of {gl} on Saturday as the government faces criticism")]
            if gl == "KE":
                cards += [gn_card("Kenya Post", f"https://m.kenyapost.example/{i}", f"Nairobi story {i}") for i in range(3)]
            return httpx.Response(200, text=gn_page(cards))
        return httpx.Response(200, text=gn_page())

    c.discovery.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    eds = c.discovery.editions_wanted()
    c.repo.kv_set("editions_cursor", 2 * len(eds) + eds.index("en-KE"))         # start the sweep at Kenya's national page
    c.settings.set_many({"search.embeddings": "hashed", "discovery.web_min_queries": 1, "discovery.edition_pages": 20, "discovery.gnews_mode": "web"})
    return c


async def test_editions_sweep_reads_only_allowed_pages_finds_publishers_and_stores_known_publishers_stories(editions):
    c = editions
    eds = c.discovery.editions_wanted()
    assert eds and all(e.startswith("en-") for e in eds)                           # English editions only when English is the language collected
    r = await c.discovery.run_editions()
    assert r["pages"] == 38 and not r["notes"], r                                 # 20 pages planned, each edition's menu read first; Zimbabwe's edition is gone
    assert all(u.split("?")[0].endswith("/robots.txt") or "/home?" in u or "/topics/" in u for u in c.discovery.calls)
    assert not any("/rss" in u or "/search" in u or "/read/" in u for u in c.discovery.calls)
    kp = by_domain(c, "kenyapost.example")                                         # m. is the mobile site of the same publisher
    assert kp and kp["country"] == "KE" and "national or local pages" in kp["status_note"]
    mp = by_domain(c, "multipost.example")
    assert mp and mp["country"] == "XX"                                           # on many countries' national pages: international
    assert {"en-ZW|NATION", "en-ZW|WORLD"} <= set(c.repo.kv_get("gnews_dead"))     # an edition that does not exist is not asked again
    assert c.repo.kv_get("editions_cursor") == 2 * len(eds) + eds.index("en-KE") + 20
    assert c.repo.kv_get("gnews_sections")["en-KE"]["ids"]["NATION"] == "NAT-KE"
    hindu = c.repo.one("SELECT key FROM sources WHERE domain = 'thehindu.com'")["key"]
    arts = c.repo.q("SELECT url, feed_id FROM articles WHERE source_key = %s", (hindu,))
    assert r["articles_new"] == len(arts) == 11 and all(a["feed_id"] is None and a["url"].startswith("https://www.thehindu.com/") for a in arts)
    assert not c.repo.q("SELECT 1 FROM articles WHERE url LIKE '%%news.google%%'")
    c.repo.kv_set("editions_cursor", 2 * len(eds) + eds.index("en-KE"))
    r2 = await c.discovery.run_editions()
    assert r2["pages"] == 21 and r2["articles_new"] == 0                          # menus are kept for a week (one new edition); the same stories are not stored twice
    c.settings.set_many({"discovery.edition_articles": False})
    c.repo.kv_set("editions_cursor", 2 * len(eds) + eds.index("en-KE"))
    assert "articles_new" not in await c.discovery.run_editions()


async def test_country_balance_promotes_the_thinnest_first_caps_and_parks(container):
    c = container
    c.settings.set_many({"search.embeddings": "hashed", "discovery.country_cap": 10, "discovery.country_target": 5, "discovery.web_min_queries": 1, "discovery.web_min_hits": 1})
    counts = c.repo.country_source_counts()
    assert counts.get("IN", 0) >= 10                                              # the curated list is India-heavy: India is already full
    c.repo.web_hits_merge([{"domain": "indianews.in", "name": "Indian News", "hits": 50, "queries": 3, "language": "en"},
                           {"domain": "tuvalunews.tv", "name": "Tuvalu", "hits": 2, "queries": 3, "language": "en", "nation_editions": ["TV"]},
                           {"domain": "malawi-times.mw", "name": "Malawi Times", "hits": 3, "queries": 3, "language": "en"}])
    added = await c.discovery._promote_web_hits()
    assert added == 2 and by_domain(c, "indianews.in") is None
    assert by_domain(c, "tuvalunews.tv")["country"] == "TV" and by_domain(c, "malawi-times.mw")["country"] == "MW"
    assert c.repo.one("SELECT promoted FROM web_hits WHERE domain = 'indianews.in'")["promoted"] is False    # waits for room
    # feed checks: the thinnest countries first
    order = [r["country"] for r in c.repo.candidates_for_feed_search(10, 5)]
    assert order.index("MW") < len(order) and order[0] in ("MW", "TV")
    # an over-full country: its weakest discovered sources are parked, and come back when the cap is raised
    for i in range(12):
        c.repo.candidate_add({"key": f"ke-{i}", "name": f"KE {i}", "domain": f"ke{i}.co.ke", "country": "KE", "type": "news", "language": "en", "origin": "web",
                              "mc_id": None, "homepage": None, "media_type": None, "stories_per_week": float(i), "note": "t"})
    have = c.repo.country_source_counts()["KE"]
    res = c.repo.rebalance(10)
    assert res["parked"] == have - 10 and c.repo.country_source_counts()["KE"] == 10
    assert c.repo.one("SELECT status FROM sources WHERE key = 'ke-0'")["status"] == "parked"           # lowest volume parked first
    assert c.repo.one("SELECT status FROM sources WHERE key = 'ke-11'")["status"] == "candidate"
    assert c.repo.rebalance(10) == {"parked": 0, "unparked": 0}
    assert c.repo.rebalance(100)["unparked"] == have - 10 and not c.repo.q("SELECT 1 FROM sources WHERE status = 'parked'")
    assert any(h["change"] == "unparked" for h in c.repo.source_history("ke-0"))
    assert c.repo.one("SELECT count(*) AS n FROM sources WHERE curated AND status = 'parked'")["n"] == 0
    c.repo.rebalance(10)
    assert (await c.discovery.run_rebalance())["unparked"] == have - 10                  # parking is off by default: anything parked comes back
    c.settings.set_many({"discovery.rebalance": True})
    assert (await c.discovery.run_rebalance())["parked"] == have - 10
    cov = c.discovery.coverage()
    assert cov["countries"] > 200 and cov["with_sources"] >= 3 and cov["target"] == 5


async def test_wikidata_asks_the_thinnest_countries_for_their_best_known_outlets(container):
    c = container
    asked = []

    wikidata_feeds._qids.clear()
    qid_cc: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(httpx.QueryParams(request.content.decode()))["query"]
        uri = lambda x: {"value": f"http://www.wikidata.org/entity/{x}"}
        if 'wdt:P297 "' in q:                                                      # the country's item
            cc = q.split('wdt:P297 "', 1)[1][:2]
            qid_cc[f"Q{900 + len(qid_cc)}"] = cc
            asked.append(cc)
            return httpx.Response(200, json={"results": {"bindings": [{"c": uri(f"Q{900 + len(qid_cc) - 1}")}]}})
        if "VALUES ?item" in q:                                                    # step two: details of the items found
            out = []
            for k, cc in qid_cc.items():
                if f"wd:{k}01 " in q + " ":
                    out += [{"item": uri(f"{k}01"), "itemLabel": {"value": f"{cc} Daily"}, "site": {"value": f"https://daily-{cc.lower()}.example/"}, "lang": {"value": "fr"}},
                            {"item": uri(f"{k}02"), "itemLabel": {"value": "Shared"}, "site": {"value": "https://www.facebook.com/x"}}]
            return httpx.Response(200, json={"results": {"bindings": out}})
        k = next(k for k in qid_cc if f"wd:{k} " in q)                           # step one: the best-known items of the country
        return httpx.Response(200, json={"results": {"bindings": [{"item": uri(f"{k}01"), "links": {"value": "12"}}, {"item": uri(f"{k}02"), "links": {"value": "3"}}]}})

    c.discovery.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.discovery.wd_sleep = 0
    c.settings.set_many({"discovery.wikidata_countries": 3, "sources.all_languages": True})
    r = await c.discovery.run_wikidata()
    assert r["countries"] == 3 and r["added"] == 3, r
    counts = c.repo.country_source_counts()
    assert all(counts.get(cc, 0) == 1 for cc in asked)                           # countries that had no source at all were asked first
    s = by_domain(c, f"daily-{asked[0].lower()}.example")
    assert (s["country"], s["language"], s["origin"]) == (asked[0], "fr", "wikidata") and "12 Wikipedia articles" in s["status_note"]
    assert by_domain(c, "facebook.com") is None
    r2 = await c.discovery.run_wikidata()
    assert not (set(asked[3:]) & set(asked[:3]))                                 # the next run asks the next countries


class FlakyMC(FakeMC):
    calls: list[int] = []
    fail_once = True

    async def national_collections(self, include_local=False):
        return [{"id": 1, "name": "India - National", "source_count": 5}, {"id": 2, "name": "Kenya - National", "source_count": 3}]

    async def collection_sources(self, cid):
        FlakyMC.calls.append(cid)
        if cid == 2 and FlakyMC.fail_once:
            FlakyMC.fail_once = False
            raise RuntimeError("connection reset")
        return await super().collection_sources(cid) if cid == 1 else []


async def test_directory_read_resumes_after_an_interruption(disc):
    c = disc
    FlakyMC.calls, FlakyMC.fail_once = [], True
    c.discovery.mc_factory = lambda token: FlakyMC()
    c.settings.set_many({"discovery.use_wikidata": False})
    r = await c.discovery.run_directory()
    assert "error" in r and c.repo.kv_get("mc_done") == [1]
    c.repo.kv_set("discovery_last_attempt", None)
    assert "directory" in c.discovery.due_all()                                  # an interrupted read is due again
    r = await c.discovery.run_directory()
    assert "error" not in r and FlakyMC.calls == [1, 2, 2]                       # India was not read twice
    assert c.repo.kv_get("mc_done") is None


async def test_an_edition_on_a_subdomain_does_not_hide_the_main_site(container):
    c = container
    c.repo.candidate_add({"key": "francais-rt", "name": "RT en français", "domain": "francais.rt.com", "country": "RU", "type": "news", "language": "fr", "origin": "wikidata",
                          "mc_id": None, "homepage": None, "media_type": None, "stories_per_week": None, "note": "t"})
    add, known, _ = await c.discovery._adder()
    assert not known("rt.com") and known("francais.rt.com") and known("feeds.bbc.com")       # bbc.com is in the starter list
    assert add({"name": "RT", "domain": "rt.com", "country": "RU", "type": "news", "language": "en", "origin": "mediacloud", "mc_id": None, "homepage": None,
                "media_type": None, "stories_per_week": None, "note": "t"})
    assert known("rt.com") and known("news.rt.com")


@pytest.fixture
def rss_editions(container):
    c = container
    c.discovery.page_sleep = c.discovery.decode_sleep = 0
    c.discovery.calls = []
    links = {}

    def handler(request: httpx.Request) -> httpx.Response:
        u = request.url
        c.discovery.calls.append(str(u))
        dec = gn_decoder(request, links)
        if dec is not None:
            return dec
        gl = u.params.get("gl", "")
        if gl == "ZW":
            return httpx.Response(404)
        if "/section/geo/" in u.path:
            city = u.path.rsplit("/", 1)[1].split("%2C")[0].split(",")[0]
            gid = f"L{gl}{abs(hash(city)) % 1000}"
            links[gid] = f"https://www.thehindu.com/news/cities/local-{gid.lower()}/article9{len(links)}.ece"
            return httpx.Response(200, text=gn_rss([("City Times", f"https://www.citytimes-{gl.lower()}.example", f"Local story in {city}", f"C{gid}"),
                                                    ("The Hindu", "https://www.thehindu.com", f"Ward councillors in {city} meet residents over drainage complaints", gid)]))
        if u.path.endswith("/NATION"):
            items = [("Reuters", "https://www.reuters.com", f"Reuters story {gl}", f"R{gl}"), ("Multi Post", "https://multipost.example", f"Multi {gl}", f"M{gl}")]
            if gl == "KE":
                items += [("Kenya Post", "https://m.kenyapost.example", f"Nairobi story {i}", f"K{i}") for i in range(3)]
            hid = f"H{gl}"
            links[hid] = f"https://www.thehindu.com/news/international/rally-in-{gl.lower()}/article1{len(links)}.ece"
            items.append(("The Hindu", "https://www.thehindu.com", f"Opposition leaders hold a large rally in the capital of {gl} on Saturday as the government faces criticism", hid))
            return httpx.Response(200, text=gn_rss(items))
        return httpx.Response(200, text=gn_rss([]))

    c.discovery.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    eds = c.discovery.editions_wanted()
    c.repo.kv_set("editions_cursor", 2 * len(eds) + eds.index("en-KE"))
    c.settings.set_many({"search.embeddings": "hashed", "discovery.web_min_queries": 1, "discovery.edition_pages": 30, "discovery.gnews_decode_max": 8})
    c.gn_links = links
    return c


async def test_rss_editions_read_sections_and_local_pages_and_store_known_publishers_stories(rss_editions):
    c = rss_editions
    eds = c.discovery.editions_wanted()
    r = await c.discovery.run_editions()
    assert r["mode"] == "rss" and r["local_pages"] == 10 and r["pages"] == 29 and not r["notes"], r     # 20 national pages (Zimbabwe's is gone), 10 local
    assert any("/rss/headlines/section/topic/NATION?" in u for u in c.discovery.calls) and any("/rss/headlines/section/geo/" in u for u in c.discovery.calls)
    assert by_domain(c, "kenyapost.example")["country"] == "KE"                      # m. is the same publisher; seen only on Kenya's page
    assert by_domain(c, "multipost.example")["country"] == "XX"
    assert "en-ZW|NATION" in c.repo.kv_get("gnews_rss_dead")
    assert c.repo.kv_get("editions_cursor") == 2 * len(eds) + eds.index("en-KE") + 20 and len(c.repo.kv_get("geo_done")) == 10
    # 21 stories of The Hindu (11 national pages, then other sections, and 10 local pages): 8 links opened (the budget), local pages first
    assert r["google_items_known_publishers"] == len(c.gn_links) == 21 and r["links_opened"] == 8 and r["links_waiting"] == 13, r
    arts = c.repo.q("SELECT url FROM articles WHERE source_key = 'hindu'")
    assert r["articles_new"] == len(arts) == 8 and all("/news/cities/local-" in a["url"] for a in arts)
    assert not c.repo.q("SELECT 1 FROM articles WHERE url LIKE '%%news.google%%'")
    decodes = sum(1 for u in c.discovery.calls if "/rss/articles/" in u)
    c.repo.kv_set("editions_cursor", 2 * len(eds) + eds.index("en-KE"))
    c.repo.kv_set("geo_done", {})
    r2 = await c.discovery.run_editions()
    opened = [u.split("/rss/articles/")[1] for u in c.discovery.calls if "/rss/articles/" in u]
    assert r2["already_had"] >= 1 and r2["links_opened"] == 8 and len(opened) == len(set(opened)) == decodes + 8      # a link is never opened twice


async def test_story_already_collected_from_the_feed_is_not_looked_up(rss_editions):
    c = rss_editions
    from tests.conftest import rss as mkrss
    c.repo.ensure_partition(dt.date.today())
    fd = c.repo.q("SELECT f.*, s.type AS source_type, s.scope AS source_scope, s.country AS source_country, s.name AS source_name, s.language AS source_language FROM feeds f JOIN sources s ON s.key = f.source_key WHERE s.key = 'hindu' LIMIT 1")[0]
    title = "Opposition leaders hold a large rally in the capital of KE on Saturday as the government faces criticism"
    c.ingest.process_items(fd, [{"title": title, "url": "https://www.thehindu.com/feed-copy/article5.ece", "lead": "", "published": NOW}])
    c.settings.set_many({"discovery.gnews_local": False, "discovery.edition_pages": 5})
    r = await c.discovery.run_editions()
    assert r["already_had"] == 1 and r["links_opened"] == 4, r                       # Kenya's story came from the feed; 4 other countries' did not
