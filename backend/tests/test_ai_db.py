import os
import datetime as dt
import json

import httpx
import pytest

from app.ai.providers import Client, pick_model
from app.ai.router import AiUnavailable, extract_json
from app.ai.service import AiNotReady
from app.ai import validate as V
from tests.test_search_db import make_story
from tests.test_extract_db import FULL, PARA, Pages

pytestmark = pytest.mark.db
UTC = dt.timezone.utc


class FakeProviders:
    """An OpenAI-compatible server for several providers, told apart by host."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.behaviour: dict[str, object] = {}
        self.models = {"api.groq.com": ["whisper-large-v3", "llama-3.3-70b-versatile", "llama-guard-4-12b", "gemma2-9b-it"],
                       "api.mistral.ai": ["mistral-embed", "mistral-small-latest", "codestral-latest"],
                       "openrouter.ai": ["foo/bar:free", "google/gemma-4-31b-it:free", "paid/model"]}
        self.reply = lambda body: {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append((host, request.url.path))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models.get(host, ["model-a"])]})
        b = self.behaviour.get(host)
        body = json.loads(request.content)
        if isinstance(b, httpx.Response):
            return b
        out = self.reply(body) if b is None else b(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out) if isinstance(out, dict) else out}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}})


@pytest.fixture
def ai(container):
    c = container
    fake = FakeProviders()
    c.ai_router.client = Client(http=httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    c.ai_router.client.timeout = 5
    c.settings.set_many({"search.embeddings": "hashed"})
    c.fake = fake
    return c


def article_answer(body):
    user = body["messages"][1]["content"]
    assert "Paragraphs:" in user and "p1:" in user and "Text status: full text" in user
    return {"kind": "news", "neutral_headline": "Cabinet clears harbour dredging contract", "summary": "The authority said dredging would begin next month.", "summary_cite": ["p1"],
            "key_points": [{"text": "Dredging starts next month", "cite": ["p1"]}, {"text": "Invented claim with a number 9999", "cite": ["p2"]}, {"text": "Uncited claim", "cite": []},
                           {"text": "Bad id", "cite": ["p99"]}],
            "context": [], "people_and_groups": [], "numbers": [], "claims": [], "open_questions": ["Who won the tender?"], "framing_notes": "", "coverage_note": ""}


async def article_id(c):
    c.repo.ensure_partition(dt.date.today())
    sid = make_story(c, "hindu", "Harbour deal cleared", "https://pub.example/a1", "The cabinet cleared a harbour deal.")
    return c.repo.one("SELECT id FROM articles WHERE story_id = %s", (sid,))["id"], sid



async def test_no_provider_is_a_clear_message_not_a_crash(ai, client):
    c = ai
    aid, sid = await article_id(c)
    with pytest.raises(AiNotReady):
        await c.ai.article(aid)
    H = {"X-Requested-With": "news-anchor"}
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    r = client.post("/api/ai/article", json={"article_id": aid}, headers=H)
    assert r.status_code == 409 and "No AI provider" in r.json()["detail"]
    st = client.get("/api/ai/status").json()
    assert st["ready"] is False and {p["id"] for p in st["providers"]} >= {"gemini", "groq", "openrouter", "custom"}


async def test_article_reading_is_validated_cached_and_logged(ai):
    c = ai
    c.extract.fetcher = Pages({"https://pub.example/a1": (200, FULL)})
    c.ai_router.set_provider("groq", {"key": "k-groq", "enabled": True})
    c.fake.reply = article_answer
    aid, _ = await article_id(c)
    out = await c.ai.article(aid)
    assert out["meta"]["provider"] == "groq" and out["meta"]["model"] == "llama-3.3-70b-versatile" and out["meta"]["cached"] is False       # 'auto' picked a chat model
    assert [k["text"] for k in out["key_points"]] == ["Dredging starts next month", "Invented claim with a number 9999"]                     # uncited and bad-id items dropped
    assert out["key_points"][1].get("unverified") is True and "unverified" not in out["key_points"][0]                                         # 9999 is not in p2
    assert out["quality"] == {"dropped_uncited": 2, "invalid_ids_removed": 1, "numbers_not_found": 1}
    assert out["paragraphs"][0].startswith("The harbour authority") and out["article"]["title"] == "Harbour deal cleared"
    calls = len(c.fake.calls)
    again = await c.ai.article(aid)
    assert again["meta"]["cached"] is True and len(c.fake.calls) == calls                      # no second call: the free quota is spared
    third = await c.ai.article(aid, refresh=True)
    assert third["meta"]["cached"] is False and len(c.fake.calls) > calls
    u = c.repo.ai_usage_summary()
    assert u["calls_today"] == 2 and u["today"][0]["provider"] == "groq" and u["today"][0]["tin"] == 200
    c.settings.set_many({"ai.prompt_article": "Write a very short brief."})                    # an edited prompt gives a new version: regenerated
    assert (await c.ai.article(aid))["meta"]["cached"] is False


async def test_router_falls_through_quota_auth_and_junk_replies_and_remembers(ai):
    c = ai
    c.extract.fetcher = Pages({"https://pub.example/a1": (200, FULL)})
    for pid in ("groq", "mistral", "openrouter"):
        c.ai_router.set_provider(pid, {"key": f"k-{pid}", "enabled": True})
    c.ai_router.set_order(["groq", "mistral", "openrouter"])
    c.fake.behaviour["api.groq.com"] = httpx.Response(429, json={"error": {"message": "Rate limit reached: requests per day (RPD): Limit 1000"}})
    c.fake.behaviour["api.mistral.ai"] = lambda body: "I am sorry, here is no JSON at all"
    c.fake.reply = article_answer
    aid, _ = await article_id(c)
    out = await c.ai.article(aid)
    assert out["meta"]["provider"] == "openrouter" and out["meta"]["model"] == "google/gemma-4-31b-it:free"
    state = {p["id"]: p for p in c.ai_router.public_state()}
    assert state["groq"]["blocked_until"] is not None and "Rate limited" in state["groq"]["last_error"]                  # daily quota: blocked until tomorrow
    first_groq = sum(1 for h, p in c.fake.calls if h == "api.groq.com" and p.endswith("/chat/completions"))
    await c.ai.article(aid, refresh=True)
    assert sum(1 for h, p in c.fake.calls if h == "api.groq.com" and p.endswith("/chat/completions")) == first_groq       # not asked again while blocked
    # every provider failing gives one clear message
    c.fake.behaviour["openrouter.ai"] = httpx.Response(401, json={"error": "bad key"})
    c.ai_router.set_provider("openrouter", {"key": "k-or2"})
    with pytest.raises(AiUnavailable) as e:
        await c.ai.article(aid, refresh=True)
    assert "groq" in str(e.value) and "openrouter" in str(e.value) and "rejected" in str(e.value)
    # the key never leaves the server
    assert "k-groq" not in json.dumps(c.ai_router.public_state(), default=str)
    # the daily cap is a hard stop
    c.settings.set_many({"ai.daily_call_cap": 1})
    c.ai_router.set_provider("openrouter", {"key": "k-or3"})
    c.ai_router.reset_provider("groq")
    with pytest.raises(AiUnavailable) as e2:
        await c.ai.article(aid, refresh=True)
    assert "daily AI call cap" in str(e2.value)


async def test_story_digest_and_scope_briefing_use_only_supplied_ids(ai):
    c = ai
    c.ai_router.set_provider("groq", {"key": "k", "enabled": True})
    c.repo.ensure_partition(dt.date.today())
    s = make_story(c, "hindu", "Cabinet clears harbour dredging deal", "https://d/1", "The cabinet cleared the deal worth 4200 crore.", topics={"politics": 1.0})
    with c.repo.pool.connection() as conn:
        now = dt.datetime.now(UTC)
        c.repo.ingest_article(conn, {"seen_day": now.date(), "source_key": "bbc", "feed_id": None, "url": "https://d/2", "url_hash": "https://d/2", "title": "Harbour dredging deal gets cabinet nod",
                                     "lead": "India approved a 4200 crore dredging contract.", "published_at": now, "seen_at": now, "country": "GB", "stype": "news"}, s,
                              {}, {"politics": 1.0}, {})
    seen = {}

    def digest(body):
        seen["user"] = body["messages"][1]["content"]
        seen["system"] = body["messages"][0]["content"]
        if "Scope:" in body["messages"][1]["content"]:
            return {"title": "World", "big_picture": [{"text": "A harbour deal.", "cite": ["S1", "S2"]}],
                    "stories": [{"story_id": "D1", "headline": "Harbour deal", "what_happened": [{"text": "Cabinet cleared it", "cite": ["S1"]}, {"text": "Ghost", "cite": ["S77"]}],
                                 "why_it_matters": [], "where_sources_differ": []}, {"story_id": "D9", "headline": "Not a real story", "what_happened": [{"text": "x", "cite": ["S1"]}]}],
                    "threads": [], "watch_next": [], "not_known_yet": ["Who bid"]}
        return {"neutral_headline": "Cabinet clears harbour deal", "summary": [{"text": "Cabinet cleared a 4200 crore deal.", "cite": ["S1", "S2"]}],
                "established": [{"text": "Both report 4200 crore", "cite": ["S1", "S2"]}], "disputed": [], "single_source": [{"text": "Bids were 9 in number", "cite": ["S1"]}],
                "official_statements": [], "social_signals": [], "framing": [], "context": [], "unknowns": [], "what_to_watch": [], "confidence": "high", "best_reads": ["S2", "S9"]}

    c.fake.reply = digest
    out = await c.ai.story(s)
    assert "S1 | " in seen["user"] and "S2 | " in seen["user"] and "reliability:" in seen["user"] and "type: news outlet" in seen["user"]
    assert "Do not mention reliability bands" in seen["system"] and "Return only JSON" in seen["system"] and "S1" in seen["system"]
    assert out["confidence"] == "high" and out["best_reads"] == ["S2"] and set(out["sources"]) == {"S1", "S2"}
    assert out["single_source"][0].get("unverified") is True                    # '9 bids' is not in the cited text
    assert out["established"][0].get("unverified") is None and out["meta"]["basis"].startswith("headlines")
    br = await c.ai.briefing("world", "")
    assert [st["story_id"] for st in br["stories"]] == ["D1"] and [x["text"] for x in br["stories"][0]["what_happened"]] == ["Cabinet cleared it"]
    assert br["scope"]["stories"]["D1"]["id"] == s and br["quality"]["invalid_ids_removed"] == 1
    c.settings.set_many({"ai.enabled": False})
    with pytest.raises(AiNotReady):
        await c.ai.briefing("world", "")



async def test_ai_api_provider_management(ai, client):
    H = {"X-Requested-With": "news-anchor"}
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    assert client.put("/api/ai/providers/nope", json={"key": "x"}, headers=H).status_code == 422
    assert client.put("/api/ai/providers/custom", json={"base_url": "ftp://x"}, headers=H).status_code == 422
    assert client.put("/api/ai/providers/groq", json={"key": "secret-key-123", "model": "llama-3.3-70b-versatile", "daily_limit": 50}, headers=H).status_code == 200
    st = client.get("/api/ai/status").json()
    g = next(p for p in st["providers"] if p["id"] == "groq")
    assert g["has_key"] and g["enabled"] and g["daily_limit"] == 50 and "secret-key-123" not in json.dumps(st)
    t = client.post("/api/ai/providers/groq/test", headers=H).json()
    assert t["ok"] is True and t["model"] == "llama-3.3-70b-versatile" and "llama-3.3-70b-versatile" in t["models"]
    assert client.put("/api/ai/order", json={"order": ["mistral", "groq"]}, headers=H).status_code == 200
    assert [p["id"] for p in client.get("/api/ai/status").json()["providers"]][:2] == ["mistral", "groq"]
    assert client.delete("/api/ai/providers/groq", headers=H).status_code == 200
    assert not next(p for p in client.get("/api/ai/status").json()["providers"] if p["id"] == "groq")["has_key"]


async def test_background_briefings_only_for_favourites_and_stop_on_provider_trouble(ai):
    c = ai
    c.ai_router.set_provider("groq", {"key": "k", "enabled": True})
    c.repo.ensure_partition(dt.date.today())
    make_story(c, "hindu", "Cabinet clears harbour dredging deal", "https://bg/1", "The cabinet cleared the deal.")
    c.repo.favourite_add("world", ""); c.repo.favourite_add("IN", "")
    c.fake.reply = lambda body: {"title": "T", "big_picture": [{"text": "A deal.", "cite": ["S1"]}], "stories": [{"story_id": "D1", "headline": "h", "what_happened": [{"text": "Cleared", "cite": ["S1"]}]}],
                                 "threads": [], "watch_next": [], "not_known_yet": []}
    r = await c.ai.background(limit=2)
    assert r["briefings"] >= 1 and r["stopped"] <= 1                         # world and India favourites exist by default; empty scopes stop quietly
    c.fake.behaviour["api.groq.com"] = httpx.Response(401, json={})
    c.repo.run("DELETE FROM ai_cache")
    c.ai_router.reset_provider("mistral")
    assert (await c.ai.background(limit=3))["briefings"] == 0


async def test_short_rate_limit_is_waited_out_once(ai, monkeypatch):
    import app.ai.router as R
    c = ai
    c.extract.fetcher = Pages({"https://pub.example/a1": (200, FULL)})
    c.ai_router.set_provider("groq", {"key": "k", "enabled": True})
    c.fake.reply = article_answer
    state = {"n": 0}

    def flaky(body):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"error": "slow down"})
        return article_answer(body)

    c.fake.behaviour["api.groq.com"] = lambda body: None
    sleeps = []

    async def fake_sleep(n):
        sleeps.append(n)
        c.fake.behaviour["api.groq.com"] = None

    monkeypatch.setattr(R.asyncio, "sleep", fake_sleep)
    c.fake.behaviour["api.groq.com"] = httpx.Response(429, headers={"retry-after": "2"}, json={"error": "slow down"})
    aid, _ = await article_id(c)
    out = await c.ai.article(aid)
    assert out["meta"]["provider"] == "groq" and sleeps and sleeps[0] <= 31          # waited once for the short limit, then succeeded


async def test_unusable_reply_is_corrected_once_on_the_same_service(ai):
    c = ai
    c.extract.fetcher = Pages({"https://pub.example/a1": (200, FULL)})
    c.ai_router.set_provider("groq", {"key": "k", "enabled": True})
    seen = []

    def first_bad_then_good(body):
        seen.append(body["messages"])
        if len(seen) == 1:
            return '{"summary": "x", "key_points": [{"text": "a" "cite": ["p1"]}'          # broken JSON
        return article_answer(body)

    c.fake.reply = first_bad_then_good
    aid, _ = await article_id(c)
    out = await c.ai.article(aid)
    assert out["meta"]["provider"] == "groq" and len(seen) == 2
    assert seen[1][-1]["role"] == "user" and "could not be used" in seen[1][-1]["content"] and seen[1][-2]["role"] == "assistant"
    from app.ai.router import extract_json
    assert extract_json('{"a": [1, 2,], "b": 1,}') == {"a": [1, 2], "b": 1}


def _digest_or_briefing(body):
    if "Scope:" in body["messages"][1]["content"]:
        return {"title": "World", "big_picture": [{"text": "A harbour deal.", "cite": ["S1"]}],
                "stories": [{"story_id": "D1", "headline": "Harbour deal", "what_happened": [{"text": "Cabinet cleared it", "cite": ["S1"]}]}],
                "threads": [], "watch_next": [], "not_known_yet": []}
    return {"neutral_headline": "Cabinet clears harbour deal", "summary": [{"text": "Cabinet cleared a deal.", "cite": ["S1", "S2"]}], "established": [], "disputed": [],
            "single_source": [], "official_statements": [], "social_signals": [], "framing": [], "context": [], "unknowns": [], "what_to_watch": [], "confidence": "high", "best_reads": ["S1"]}


async def test_prefetch_prepares_text_briefing_and_digests_then_reuses_them(ai, client):
    c = ai
    c.ai_router.set_provider("groq", {"key": "k", "enabled": True})
    c.repo.ensure_partition(dt.date.today())
    c.extract.fetcher = Pages({"https://pub.example/p1": (200, FULL)})
    s = make_story(c, "hindu", "Cabinet clears harbour dredging deal", "https://pub.example/p1", "The cabinet cleared the deal worth 4200 crore.")
    with c.repo.pool.connection() as conn:
        now = dt.datetime.now(UTC)
        c.repo.ingest_article(conn, {"seen_day": now.date(), "source_key": "bbc", "feed_id": None, "url": "https://pub.example/p2", "url_hash": "https://pub.example/p2",
                                     "title": "Harbour dredging deal gets cabinet nod", "lead": "India approved a dredging contract.", "published_at": now, "seen_at": now,
                                     "country": "GB", "stype": "news", "fp": "other"}, s, {}, {}, {})
    c.repo.run("UPDATE stories SET indep = 2, outlets = 2 WHERE id = %s", (s,))
    c.fake.reply = _digest_or_briefing
    assert c.prefetch.due()
    r = await c.prefetch.run()
    assert (r["scopes"], r["texts"], r["briefings"], r["digests"], r["stopped"]) == (1, 1, 1, 1, ""), r
    assert c.repo.text_get(c.repo.one("SELECT url_hash FROM articles WHERE url = 'https://pub.example/p1'")["url_hash"])     # the reading pane has its text
    calls = len(c.fake.calls)
    c.repo.kv_set("prefetch_last", None)
    r2 = await c.prefetch.run()
    assert r2["reused"] == 2 and r2["digests"] == 0 and len(c.fake.calls) == calls           # nothing changed: nothing made again
    H = {"X-Requested-With": "news-anchor"}
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    ready = client.get(f"/api/ai/ready?story_id={s}").json()
    assert ready["ready"] and ready["current"] is True and ready["result"]["neutral_headline"] == "Cabinet clears harbour deal"
    assert client.get("/api/ai/ready?place=world").json()["result"]["title"] == "World"
    assert client.get("/api/ai/ready?story_id=999999").json() == {"ready": False}
    client.get("/api/stories?place=IN&topic=politics")
    assert ("IN", "politics") in c.prefetch.scopes()                                      # a scope you open is prepared next time
    c.settings.set_many({"ai.prefetch_share": 0})
    c.repo.run("DELETE FROM ai_cache")
    c.repo.kv_set("prefetch_last", None)
    r3 = await c.prefetch.run()
    assert r3["briefings"] == 0 and "share" in r3["stopped"]                               # never eats the room left for what you open yourself
