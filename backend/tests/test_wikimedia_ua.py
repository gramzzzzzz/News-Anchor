import asyncio
import json

import httpx

from app.dao import wikimedia
from app.dao.discovery import wikidata_feeds
from app.dao.social import wikidata_accounts
from app.dao.trust import external


def test_wikimedia_user_agent_names_the_program_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("NA_WIKIMEDIA_USER_AGENT", raising=False)
    assert wikimedia.user_agent().startswith("NewsAnchor/")
    assert "Mozilla" not in wikimedia.user_agent()
    monkeypatch.setenv("NA_WIKIMEDIA_USER_AGENT", "NewsAnchor/1.0 (me@example.org)")
    assert wikimedia.user_agent() == "NewsAnchor/1.0 (me@example.org)"


def test_every_wikidata_request_sends_it_not_the_browsers(monkeypatch):
    monkeypatch.delenv("NA_WIKIMEDIA_USER_AGENT", raising=False)
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers["user-agent"])
        return httpx.Response(200, text=json.dumps({"results": {"bindings": []}}))

    async def run():
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 Chrome/122"}, transport=httpx.MockTransport(handler)) as c:
            await wikidata_feeds.outlets(c)
            await wikidata_accounts.accounts(c, ["bbc.com"])
            await external.wikidata_owners(c, ["bbc.com"])

    asyncio.run(run())
    assert len(seen) == 3 and all(u.startswith("NewsAnchor/") for u in seen)


def _rows(*items):
    return httpx.Response(200, text=json.dumps({"results": {"bindings": list(items)}}))


def _uri(q):
    return {"type": "uri", "value": f"http://www.wikidata.org/entity/{q}"}


def test_country_outlets_asks_two_small_questions_and_drops_closed_and_siteless_ones():
    asked: list[str] = []
    wikidata_feeds._qids.clear()

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.content.decode()
        asked.append(q)
        if "P297" in q:
            return _rows({"c": _uri("Q142")})
        if "VALUES" in q:                                    # step two: the details of the items found
            return _rows(
                {"item": _uri("Q1"), "itemLabel": {"value": "Le Monde"}, "site": {"value": "https://lemonde.fr"}, "lang": {"value": "fr"}},
                {"item": _uri("Q2"), "itemLabel": {"value": "Old Paper"}, "site": {"value": "https://old.example"}, "end": {"value": "1999"}},
                {"item": _uri("Q4"), "itemLabel": {"value": "Le Figaro"}, "site": {"value": "https://lefigaro.fr"}})
        return _rows({"item": _uri("Q1"), "links": {"value": "90"}}, {"item": _uri("Q2"), "links": {"value": "80"}},
                     {"item": _uri("Q3"), "links": {"value": "70"}}, {"item": _uri("Q4"), "links": {"value": "60"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await wikidata_feeds.country_outlets(c, "fr", limit=5)

    outs = asyncio.run(run())
    assert [o["qid"] for o in outs] == ["Q1", "Q4"]          # Q2 has closed, Q3 has no website
    assert outs[0]["language"] == "fr" and outs[0]["links"] == 90 and outs[0]["country"] == "FR"
    step_one = next(q for q in asked if "sitelinks" in q and "VALUES" not in q)
    assert "wd%3AQ142" in step_one or "wd:Q142" in step_one


def test_a_busy_wikidata_front_server_is_asked_again(monkeypatch):
    waits: list[float] = []

    async def no_sleep(s):
        waits.append(s)

    monkeypatch.setattr(wikidata_feeds.asyncio, "sleep", no_sleep)
    codes = iter([502, 503, 200])

    def handler(req: httpx.Request) -> httpx.Response:
        c = next(codes)
        return _rows() if c == 200 else httpx.Response(c, text="bad gateway")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await wikidata_feeds.outlets(c)

    assert asyncio.run(run()) == [] and waits == [5, 20]

    def always(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async def fail():
        async with httpx.AsyncClient(transport=httpx.MockTransport(always)) as c:
            await wikidata_feeds.outlets(c)

    import pytest
    with pytest.raises(RuntimeError, match="502"):
        asyncio.run(fail())
