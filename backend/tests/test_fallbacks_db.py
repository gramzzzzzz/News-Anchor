"""Collecting from sites without a feed (news sitemaps, home pages), community link sites, robots.txt, and swapping for reliability."""
import datetime as dt

import pytest

from tests.conftest import FakeFetcher, rss

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)


def news_sitemap(prefix, n=5):
    urls = "".join(f'<url><loc>https://{prefix}.example/story/{i}-headline-words-here</loc><news:news><news:publication><news:name>P</news:name></news:publication>'
                   f'<news:publication_date>{(NOW - dt.timedelta(hours=i + 1)).isoformat()}</news:publication_date><news:title>{prefix} sitemap story {i} about the harbour council</news:title></news:news></url>'
                   for i in range(n))
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">{urls}</urlset>'.encode()


def home(links):
    a = "".join(f'<li><a href="{u}">{t}</a></li>' for u, t in links)
    nav = '<a href="/category/sport/">Sport section</a><a href="/about-us">About us and our team</a><a href="https://other.example/x-y-z-w">Elsewhere is not our site at all</a>'
    return f"<html><body><nav>{nav}</nav><ul>{a}</ul></body></html>".encode()


def links(n, start=0):
    return [(f"/news/2026/11/{start + i:02d}/council-meets-on-water-supply-{start + i}", f"Council meets residents over the water supply plan number {start + i}") for i in range(n)]


def candidate(c, key, domain):
    c.repo.candidate_add({"key": key, "name": key.title(), "domain": domain, "country": "KE", "type": "news", "language": "en", "origin": "web", "mc_id": None,
                          "homepage": f"https://{domain}/", "media_type": None, "stories_per_week": None, "note": "t"})


async def test_feedless_sites_are_read_from_their_news_sitemap_or_home_page(container):
    c = container
    c.repo.ensure_partition(dt.date.today())
    f = FakeFetcher({"https://smap.example/": b"<html><body>no feeds</body></html>",
                     "https://smap.example/sitemaps/index.xml": b'<sitemapindex><sitemap><loc>https://smap.example/sitemaps/news-1.xml</loc><lastmod>2026-11-01</lastmod></sitemap>'
                                                                b'<sitemap><loc>https://smap.example/sitemaps/posts.xml</loc></sitemap></sitemapindex>',
                     "https://smap.example/sitemaps/news-1.xml": news_sitemap("smap"),
                     "https://homeonly.example/": home(links(10)),
                     "https://thin.example/": home(links(3))},
                    sitemaps={"smap.example": ["https://smap.example/sitemaps/index-news.xml", "https://smap.example/sitemap.xml"]})
    f.feeds["https://smap.example/sitemaps/index-news.xml"] = f.feeds.pop("https://smap.example/sitemaps/index.xml")
    c.discovery.fetcher = c.ingest.fetcher = c.fetcher = f
    for k, d in (("smap", "smap.example"), ("homeonly", "homeonly.example"), ("thin", "thin.example")):
        candidate(c, k, d)
    res = await c.discovery.run_batch(10)
    assert res["found"] == 2 and res["none"] == 1, res
    feeds = {r["source_key"]: r for r in c.repo.q("SELECT source_key, kind, url FROM feeds WHERE source_key IN ('smap', 'homeonly', 'thin')")}
    assert feeds["smap"]["kind"] == "sitemap" and feeds["smap"]["url"] == "https://smap.example/sitemaps/index-news.xml"
    assert feeds["homeonly"]["kind"] == "homepage" and "thin" not in feeds                  # three article links are not a news home page
    assert c.repo.one("SELECT status FROM sources WHERE key = 'thin'")["status"] == "no_feed"
    c.repo.feeds_poll_now()
    st = await c.ingest.run_cycle()
    smap = c.repo.q("SELECT title, url FROM articles WHERE source_key = 'smap'")
    assert len(smap) == 5 and smap[0]["url"].startswith("https://smap.example/story/")
    assert not c.repo.q("SELECT 1 FROM articles WHERE source_key = 'homeonly'")              # first visit: only remembers what is there
    f.feeds["https://homeonly.example/"] = home(links(2, start=20) + links(10))
    c.repo.feeds_poll_now()
    await c.ingest.run_cycle()
    got = c.repo.q("SELECT title, url, published_at FROM articles WHERE source_key = 'homeonly' ORDER BY title")
    assert [g["title"] for g in got] == ["Council meets residents over the water supply plan number 20", "Council meets residents over the water supply plan number 21"]
    assert all(g["url"].startswith("https://homeonly.example/news/2026/11/2") for g in got)
    poll = c.repo.one("SELECT poll_interval_s FROM feeds WHERE source_key = 'homeonly'")["poll_interval_s"]
    assert poll >= 1800                                                                          # a whole page: at most every 30 minutes


def test_home_page_links_are_told_apart_from_navigation():
    from app.dao.feeds import homepage
    got = homepage.article_links(home(links(3)).decode(), "https://www.homeonly.example/")
    assert [g["url"] for g in got] == [f"https://www.homeonly.example/news/2026/11/{i:02d}/council-meets-on-water-supply-{i}" for i in range(3)]


async def test_community_items_are_credited_to_collected_outlets(container):
    c = container
    c.repo.ensure_partition(dt.date.today())
    feed = rss([{"title": "Council approves the harbour plan after a long debate (thehindu.com)", "link": "https://www.thehindu.com/news/cities/harbour-plan/article77.ece", "pub": NOW},
                {"title": "A small blog post about compilers and their history (smallblog.example)", "link": "https://smallblog.example/posts/compilers-history", "pub": NOW},
                {"title": "Ask HN: what are you reading", "link": "https://news.ycombinator.com/item?id=1", "pub": NOW}])
    url = c.repo.one("SELECT url FROM feeds WHERE source_key = 'hn' AND state <> 'retired'")["url"]
    c.fetcher.feeds[url] = feed
    c.repo.run("UPDATE feeds SET next_poll_at = now() + interval '1 day'")
    c.repo.run("UPDATE feeds SET next_poll_at = now() WHERE url = %s", (url,))
    st = await c.ingest.run_cycle()
    arts = {r["url"]: r for r in c.repo.q("SELECT source_key, url, title FROM articles")}
    assert arts["https://www.thehindu.com/news/cities/harbour-plan/article77.ece"]["source_key"] == "hindu"
    assert arts["https://www.thehindu.com/news/cities/harbour-plan/article77.ece"]["title"] == "Council approves the harbour plan after a long debate"
    assert arts["https://smallblog.example/posts/compilers-history"]["source_key"] == "hn"
    assert len(arts) == 2 and st["articles_new"] == 2                                          # the discussion post has no outside link


def test_community_feeds_that_link_their_own_pages():
    from app.dao.feeds import community
    body = ('<?xml version="1.0"?><rss version="2.0"><channel><title>Techmeme</title><item><title>Chipmaker raises its forecast (Jane Doe/Reuters)</title>'
            '<link>https://www.techmeme.com/261101/p9</link><description>&lt;a href="https://www.techmeme.com/261101/p9"&gt;x&lt;/a&gt; '
            '&lt;a href="https://www.reuters.com/technology/chipmaker-forecast-2026-11-01/"&gt;Reuters&lt;/a&gt;</description></item>'
            '<item><title>Home page only</title><link>https://www.techmeme.com/261101/p8</link><description>&lt;a href="https://www.thetimes.com/"&gt;x&lt;/a&gt;</description></item>'
            '</channel></rss>').encode()
    items, ok = community.parse(body, "https://www.techmeme.com/feed.xml")
    assert ok and [(i["title"], i["url"]) for i in items] == [("Chipmaker raises its forecast", "https://www.reuters.com/technology/chipmaker-forecast-2026-11-01/")]


def test_robots_rules_follow_rfc_9309():
    from app.dao.fetch.robots import robots_allows
    hn = "User-Agent: *\nCrawl-delay: 30\nDisallow: /r?\nDisallow: /x?\n"
    assert robots_allows(hn, "https://news.ycombinator.com/rss", "NewsAnchor") and not robots_allows(hn, "https://news.ycombinator.com/r?id=1", "NewsAnchor")
    gn = "User-agent: *\nDisallow: /\nAllow: /topics/\nAllow: /home$\n"
    assert robots_allows(gn, "https://news.google.com/topics/x", "NewsAnchor") and not robots_allows(gn, "https://news.google.com/home/x", "NewsAnchor")
    assert not robots_allows("User-agent: newsanchor\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "https://a.example/x", "NewsAnchor")


def _src(c, key, band, country="KE", curated=False, origin="web", lang="en"):
    c.repo.run("""INSERT INTO sources (key, name, domain, country, type, scope, curated, status, band, origin, language)
                  VALUES (%s, %s, %s, %s, 'news', 'national', %s, 'active', %s, %s, %s)""", (key, key, f"{key}.example", country, curated, band, origin, lang))
    c.repo.run("INSERT INTO feeds (source_key, url, kind) VALUES (%s, %s, 'rss')", (key, f"https://{key}.example/rss"))


def _arts(c, key, topics, places=(), n=3):
    for i in range(n):
        c.repo.run("""INSERT INTO articles (seen_day, source_key, url, url_hash, title, lead, published_at, seen_at, topics, places)
                      VALUES (current_date, %s, %s, %s, 't', '', now(), now(), %s, %s)""", (key, f"https://{key}.example/{i}{topics}", f"{key}{i}{topics}", list(topics), list(places)))


def test_swaps_raise_reliability_without_losing_any_place_subject_or_language(container):
    c = container
    c.repo.ensure_partition(dt.date.today())
    c.repo.run("UPDATE sources SET status = 'retired' WHERE curated = true")                 # only the sources of this test count
    for k in ("ke1", "ke2", "ke3", "ke4"):
        _src(c, k, "reliable")
        _arts(c, k, ["politics", "politics.elections"])
    _src(c, "commonmix", "mixed")                                                              # covers only what four others cover
    _arts(c, "commonmix", ["politics", "politics.elections"])
    _src(c, "rarelow", "low")                                                                  # the only source on a city and on farming
    _arts(c, "rarelow", ["economy", "economy.agriculture"], ["KE-30-nakuru"])
    _src(c, "swahili", "low", lang="sw")                                                       # the only Swahili source of Kenya
    _src(c, "unrated", "unrated")
    _arts(c, "unrated", ["politics", "politics.elections"])
    _src(c, "newcomer", "reliable", origin="ratings")
    _src(c, "usnew", "highly_reliable", country="US", origin="ratings")                      # no weaker US source to replace
    for i in range(92):                                                                        # the collection is over its limit of 100 by 2
        c.repo.run("INSERT INTO sources (key, name, domain, country, type, scope, curated, status) VALUES (%s, 'f', %s, 'GB', 'news', 'national', false, 'candidate')",
                   (f"filler{i}", f"filler{i}.example"))
    c.settings.set_many({"discovery.max_total": 100})
    up = c.discovery.upgrader
    before = up.measure()
    res = up.swap()
    assert res["swapped"] == 1, res
    st = {r["key"]: r["status"] for r in c.repo.q("SELECT key, status FROM sources WHERE key IN ('commonmix', 'rarelow', 'swahili', 'unrated', 'newcomer')")}
    assert st == {"commonmix": "swapped", "rarelow": "active", "swahili": "active", "unrated": "active", "newcomer": "active"}
    assert "Swapped for newcomer" in c.repo.one("SELECT status_note FROM sources WHERE key = 'commonmix'")["status_note"]
    after = up.measure()
    assert after["cells"] == before["cells"] and after["countries"] == before["countries"] and after["reliable_share_sources"] > before["reliable_share_sources"]
    assert up.swap()["swapped"] == 0                                                           # each newcomer replaces at most one
    c.settings.set_many({"discovery.upgrade": False})
    import asyncio
    assert asyncio.run(up.run())["restored"] == 1 and c.repo.one("SELECT status FROM sources WHERE key = 'commonmix'")["status"] == "active"
