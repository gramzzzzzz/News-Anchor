import os
import datetime as dt
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)


def iso(minutes_ago: int) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


BSKY_FEED = {"feed": [
    {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3kx1", "author": {"handle": "reuters.com"}, "indexedAt": iso(5),
              "record": {"text": "Exchange hacked as thieves take $400 million in crypto. Withdrawals are paused.", "createdAt": iso(5)},
              "embed": {"external": {"uri": "https://www.reuters.com/tech/exchange-hack", "title": "Exchange hacked"}}}},
    {"post": {"uri": "at://did:plc:abc/app.bsky.feed.post/3kx2", "author": {"handle": "reuters.com"}, "record": {"text": "", "createdAt": iso(9)}}},
]}


@pytest.fixture
def social(container):
    c = container
    c.calls = []
    state = {"x_posts": 3}

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        c.calls.append((request.method, u))
        host = request.url.host
        if host == "public.api.bsky.app":
            return httpx.Response(200, json=BSKY_FEED)
        if host == "bsky.social" and request.url.path.endswith("createSession"):
            body = json.loads(request.content)
            if body["password"] != "app-pass":
                return httpx.Response(401, json={"error": "AuthenticationRequired"})
            return httpx.Response(200, json={"accessJwt": "A", "refreshJwt": "R", "did": "did:plc:me", "handle": body["identifier"]})
        if host == "bsky.social" and request.url.path.endswith("getTimeline"):
            return httpx.Response(200, json=BSKY_FEED) if request.headers.get("authorization") == "Bearer A" else httpx.Response(401)
        if host == "www.googleapis.com":
            assert request.url.params["playlistId"] == "UU" + "x" * 22 and request.url.params["key"] == "ytkey"
            return httpx.Response(200, json={"items": [{"snippet": {"title": "Exchange hack explained", "channelTitle": "Reuters", "publishedAt": iso(30), "description": "What happened.",
                                                                   "resourceId": {"videoId": "abc123"}}}, {"snippet": {"title": "Private video", "resourceId": {"videoId": "zz"}}}]})
        if host == "www.reddit.com" and request.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "rt", "expires_in": 3600})
        if host == "oauth.reddit.com":
            assert request.headers["user-agent"].startswith("server:news-anchor")
            return httpx.Response(200, json={"data": {"children": [
                {"data": {"title": "Exchange loses $400 million in hack", "permalink": "/r/worldnews/comments/1/x/", "url": "https://apnews.com/a", "is_self": False,
                          "subreddit": "worldnews", "created_utc": NOW.timestamp() - 600}},
                {"data": {"title": "Pinned rules", "permalink": "/r/worldnews/comments/2/y/", "stickied": True, "subreddit": "worldnews", "created_utc": NOW.timestamp()}}]}})
        if host == "api.x.com" and request.url.path == "/2/oauth2/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code_verifier"][0]
            return httpx.Response(200, json={"access_token": "xa", "refresh_token": "xr", "expires_in": 7200})
        if host == "api.x.com" and request.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "42", "username": "owner"}})
        if host == "api.x.com" and "/timelines/reverse_chronological" in request.url.path:
            n = state["x_posts"]
            return httpx.Response(200, json={"data": [{"id": str(100 + i), "text": f"Breaking: exchange hacked, update {i}", "author_id": "7", "created_at": iso(3)} for i in range(n)],
                                             "includes": {"users": [{"id": "7", "username": "AP"}]}, "meta": {"newest_id": str(100 + n - 1), "result_count": n}})
        return httpx.Response(404)

    c.social.http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.x_state = state
    return c


async def test_settings_become_social_sources_and_removing_them_retires_them(social):
    c = social
    c.settings.set_many({"social.bluesky.accounts": "reuters.com\n@apnews.com", "social.mastodon.accounts": "@lemonde@mastodon.social",
                         "social.mastodon.tags": "mastodon.social/news", "social.reddit.subreddits": "worldnews\nr/india"})
    r = await c.social.sync(force=True)
    assert r["feeds"] == 6, r
    feeds = {f["url"]: f for f in c.repo.q("SELECT f.url, f.kind, f.state, s.type, s.status, s.name FROM feeds f JOIN sources s ON s.key = f.source_key WHERE f.origin = 'social'")}
    assert feeds["bluesky:author:reuters.com"]["kind"] == "bluesky" and feeds["bluesky:author:apnews.com"]["name"] == "@apnews.com on Bluesky"
    assert feeds["https://mastodon.social/@lemonde.rss"]["kind"] == "rss" and "https://mastodon.social/tags/news.rss" in feeds
    assert feeds["https://www.reddit.com/r/india/.rss"]["kind"] == "rss"            # no Reddit login: the RSS feeds (until Reddit ends them)
    assert all(f["type"] == "social" for f in feeds.values())
    assert (await c.social.sync())["unchanged"] is True                             # nothing changed, nothing done
    c.settings.set_many({"social.bluesky.accounts": "reuters.com"})
    r = await c.social.sync()
    assert r["retired"] == 1
    gone = c.repo.one("SELECT f.state, s.status FROM feeds f JOIN sources s ON s.key = f.source_key WHERE f.url = 'bluesky:author:apnews.com'")
    assert gone["state"] == "retired" and gone["status"] == "retired"
    c.settings.set_many({"social.bluesky.accounts": "reuters.com\napnews.com"})
    await c.social.sync()
    assert c.repo.one("SELECT state FROM feeds WHERE url = 'bluesky:author:apnews.com'")["state"] == "active"       # asking again brings it back
    assert not any(s["key"].startswith("social-") for s in c.repo.sources_for_trust())                              # the trust crawler skips social accounts
    c.settings.set_many({"social.enabled": False})
    await c.social.sync()
    assert c.repo.one("SELECT count(*) AS n FROM feeds WHERE origin = 'social' AND state <> 'retired'")["n"] == 0


async def test_social_posts_are_collected_marked_social_and_kept_out_of_top(social):
    c = social
    c.settings.set_many({"social.bluesky.accounts": "reuters.com"})
    await c.social.sync(force=True)
    c.repo.run("UPDATE feeds SET next_poll_at = now() WHERE origin = 'social'")
    res = await c.ingest.run_cycle()
    assert res["articles_new"] == 1, res                                             # the empty post is skipped
    a = c.repo.one("SELECT a.title, a.url, a.lead, s.type FROM articles a JOIN sources s ON s.key = a.source_key")
    assert a["title"].startswith("@reuters.com: Exchange hacked") and a["url"] == "https://bsky.app/profile/reuters.com/post/3kx1" and a["type"] == "social"
    assert "https://www.reuters.com/tech/exchange-hack" in a["lead"]
    assert c.stories.list("world", "", "top")["stories"] == []                       # social alone never reaches Top
    assert c.stories.list("world", "", "latest")["stories"] == []                    # nor Latest: it needs different news outlets


async def test_bluesky_login_reads_the_timeline_and_reports_a_bad_password(social):
    c = social
    c.settings.set_many({"social.bluesky.handle": "me.bsky.social", "social.bluesky.app_password": "wrong"})
    await c.social.sync(force=True)
    f = c.repo.one("SELECT * FROM feeds WHERE url = 'bluesky:timeline'")
    posts, ok, err = await c.social.fetch(f)
    assert not ok and "did not accept" in err
    c.settings.set_many({"social.bluesky.app_password": "app-pass"})
    c.social._bsky = None
    posts, ok, err = await c.social.fetch(f)
    assert ok and len(posts) == 1 and err is None


async def test_reddit_login_reads_through_the_api(social):
    c = social
    c.settings.set_many({"social.reddit.client_id": "id", "social.reddit.client_secret": "sec", "social.reddit.username": "me", "social.reddit.password": "pw",
                         "social.reddit.subreddits": "worldnews"})
    await c.social.sync(force=True)
    urls = {f["url"] for f in c.repo.social_feeds()}
    assert urls == {"reddit:home", "reddit:r/worldnews"}
    posts, ok, _ = await c.social.fetch({"url": "reddit:r/worldnews", "kind": "reddit"})
    assert ok and [p["title"] for p in posts] == ["Exchange loses $400 million in hack"]                 # pinned posts are skipped
    assert posts[0]["url"] == "https://www.reddit.com/r/worldnews/comments/1/x/" and "apnews.com" in posts[0]["lead"]
    assert sum(1 for m, u in c.calls if "access_token" in u) == 1
    await c.social.fetch({"url": "reddit:home", "kind": "reddit"})
    assert sum(1 for m, u in c.calls if "access_token" in u) == 1                    # the token is reused until it expires


async def test_x_login_with_pkce_then_reads_within_the_monthly_budget(social, client):
    c = social
    H = {"X-Requested-With": "news-anchor"}
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    assert client.get("/api/social/x/login", follow_redirects=False).status_code == 409             # no client id yet
    c.settings.set_many({"social.x.client_id": "cid", "social.x.monthly_reads": 5, "social.x.per_poll": 30})
    r = client.get("/api/social/x/login", follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.headers["location"].startswith("https://x.com/i/oauth2/authorize") and q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cid"]
    assert q["redirect_uri"][0].endswith("/api/social/x/callback")
    client.cookies.clear()                                                           # X sends the browser back without our cookie
    bad = client.get("/api/social/x/callback?code=abc&state=forged", follow_redirects=False)
    assert bad.status_code == 303 and "x-error" in bad.headers["location"]
    ok = client.get(f"/api/social/x/callback?code=abc&state={q['state'][0]}", follow_redirects=False)
    assert ok.status_code == 303 and "x-ok" in ok.headers["location"] and "owner" in ok.headers["location"]
    assert c.repo.kv_get("x_tokens")["uid"] == "42" and c.repo.kv_get("x_pkce") is None              # the state works once
    assert any(f["url"] == "x:home" for f in c.repo.social_feeds())
    posts, ok_, _ = await c.social.fetch({"url": "x:home", "kind": "x"})
    assert ok_ and len(posts) == 3 and posts[0]["url"] == "https://x.com/AP/status/100"
    assert c.social.x_budget()["used"] == 3 and c.repo.kv_get("x_since:x:home") == "102"
    c.x_state["x_posts"] = 2
    await c.social.fetch({"url": "x:home", "kind": "x"})
    assert c.social.x_budget()["left"] == 0
    n = len(c.calls)
    posts, ok_, note = await c.social.fetch({"url": "x:home", "kind": "x"})
    assert posts == [] and ok_ and "budget" in note and len(c.calls) == n            # budget spent: X is not asked (or paid) at all


def test_titleless_feed_items_use_their_text():
    from app.dao.feeds.rss import parse_feed
    body = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>@lemonde</title><item><link>https://mastodon.social/@lemonde/1</link>'
            b'<description>&lt;p&gt;Le gouvernement annonce une r\xc3\xa9forme. Suite du texte.&lt;/p&gt;</description></item></channel></rss>')
    items, ok = parse_feed(body)
    assert ok and items[0]["title"] == "Le gouvernement annonce une réforme."


async def test_youtube_needs_a_key_and_is_read_through_the_api(social):
    c = social
    cid = "UC" + "x" * 22
    c.settings.set_many({"social.youtube.channels": cid})
    await c.social.sync(force=True)
    assert not c.repo.social_feeds()                                                 # no key: YouTube is not read (its robots.txt forbids the feeds)
    c.settings.set_many({"social.youtube.api_key": "ytkey"})
    await c.social.sync(force=True)
    f = c.repo.social_feeds()
    assert [x["url"] for x in f] == [f"youtube:{cid}"] and f[0]["kind"] == "youtube"
    posts, ok, _ = await c.social.fetch({"url": f"youtube:{cid}", "kind": "youtube"})
    assert ok and [p["title"] for p in posts] == ["Exchange hack explained"] and posts[0]["url"] == "https://www.youtube.com/watch?v=abc123"
