import os
import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from app.dao.trust import lin2023, wikipedia_rsp
from tests.conftest import FakeFetcher

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
H = {"X-Requested-With": "news-anchor"}
RSP_HTML = (Path(__file__).parent / "fixtures" / "rsp_excerpt.html").read_text(encoding="utf-8")
CSV = (b"domain,pc1,afm,fc,mbfc,lewandowsky_mean,misinfome_bin\n"
       b"thehindu.com,0.705,0.868742942810059,0.602589070796967,0.75,0.875,1\n"
       b"bbc.com,0.88,0.8137,1,0.8333,0.875,1\nbbc.co.uk,0.72,0.7401,0.66123456789,0.8333,0.875,1\n")
HOME = b"""<html><body><footer><a href='/about'>About us</a><a href='/contact'>Contact</a><a href='/team'>Our team</a></footer></body></html>"""


def web(request: httpx.Request) -> httpx.Response:
    if request.url.host == "rdap.org":
        return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "2001-05-01T00:00:00Z"}]})
    if request.url.host == "query.wikidata.org":
        owners = {"results": {"bindings": [{"site": {"value": "https://www.thehindu.com/"}, "item": {"value": "http://www.wikidata.org/entity/Q926175"},
                                            "itemLabel": {"value": "The Hindu"}, "owners": {"value": "The Hindu Group"}}]}}
        return httpx.Response(200, text=json.dumps(owners))
    return httpx.Response(404)


@pytest.fixture
def trust(container):
    c = container
    c.repo.upsert_source({"key": "dailymail", "name": "Daily Mail", "domain": "dailymail.co.uk", "country": "GB", "type": "news", "scope": "international"})
    c.trust.fetcher = FakeFetcher({lin2023.URL: CSV, wikipedia_rsp.API: json.dumps({"parse": {"text": RSP_HTML}}).encode(), "https://thehindu.com/": HOME})
    c.trust.rdap_pause = 0
    c.trust.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(web))
    return c


def src(c, key):
    return next(s for s in c.repo.list_sources() if s["key"] == key)


async def test_crawl_scores_every_source_from_evidence(trust):
    c = trust
    c.settings.set_many({"trust.calibrate": False})                  # the weights as set, so the score can be checked by hand
    res = await c.trust.refresh()
    assert res["sources"] == 167 and res["bands"]["blocked"] == 1
    hindu, bbc, mail, dtnext, sport = (src(c, k) for k in ("hindu", "bbc", "dailymail", "dtnext", "sportstar"))
    assert (hindu["band"], bbc["band"], mail["band"], dtnext["band"]) == ("reliable", "highly_reliable", "blocked", "unrated")
    assert sport["band"] == "reliable"                                   # a section site inherits its publisher's evidence
    # The Hindu, by hand: lin .705*3, wikipedia .85*3, transparency 3 of 6 -> .5+.5*.5 = .75*1, owner .75*.5, age .9*.5, prior .5*1
    exp = (0.5 + 3 * 0.705 + 3 * 0.85 + 1 * 0.75 + 0.5 * 0.75 + 0.5 * 0.9) / (1 + 3 + 3 + 1 + 0.5 + 0.5)
    assert hindu["score"] == pytest.approx(exp, abs=1e-4) and hindu["confidence"] == pytest.approx(8 / 10, abs=1e-4)
    sigs = {x["signal"]: x for x in c.repo.signals_for("hindu")}
    assert set(sigs) == {"lin2023", "wikipedia_rsp", "transparency", "domain_age", "ownership"}
    assert sigs["wikipedia_rsp"]["evidence_url"].endswith("WP:THEHINDU") and "generally reliable" in sigs["wikipedia_rsp"]["detail"]
    assert "About page" in sigs["transparency"]["detail"] and "Corrections policy" in sigs["transparency"]["detail"]
    assert hindu["owner"] == "The Hindu Group" and not hindu["provisional"] and hindu["trust_updated_at"]
    assert mail["visible"] and dtnext["visible"] and hindu["visible"]               # nothing is hidden by default; the card says what the evidence is
    assert c.repo.kv_get("trust_last_run")["sources"] == 167


async def test_floor_changes_what_lists_show_without_a_new_crawl(trust):
    c = trust
    await c.trust.refresh()
    feeds = {f["source_key"]: f for f in c.repo.all_feeds()}
    now = dt.datetime.now(UTC)
    t = lambda k, title, u: c.ingest.process_items(feeds[k], [{"title": title, "url": u, "lead": "", "published": now}])
    t("hindu", "Mahabalipuram shore temple gets new lighting plan", "https://x.example/1")
    t("bbc", "Mahabalipuram shore temple gets a new lighting plan", "https://x.example/2")
    t("dtnext", "Madurai flower market prices rise before festival", "https://x.example/3")
    lst = lambda: {s["title"][:12]: s for s in c.stories.list("world", "", "top")["stories"]}
    both = lst()["Mahabalipura"]
    assert both["outlets"] == 2 and sorted(x["b"] for x in both["strip"]) == ["highly_reliable", "reliable"]
    assert "Madurai flow" in lst()                                       # unrated but shown by default
    c.settings.set_many({"sources.floor": "reliable", "sources.show_unrated": True}); c.trust.rescore()
    assert "Madurai flow" in lst()                                       # a chosen floor keeps unrated sources while show_unrated is on
    c.settings.set_many({"sources.show_unrated": False}); c.trust.rescore()
    assert "Madurai flow" not in lst()
    c.settings.set_many({"sources.floor": "highly_reliable"}); c.trust.rescore()
    only_bbc = lst()["Mahabalipura"]
    assert only_bbc["outlets"] == 1 and [x["k"] for x in only_bbc["strip"]] == ["bbc"]       # The Hindu is below this floor now
    c.settings.reset(); c.trust.rescore()
    assert lst()["Mahabalipura"]["outlets"] == 2 and "Madurai flow" in lst()
    mail = src(c, "dailymail")
    assert mail["visible"]                                              # a source Wikipedia deprecates is still shown by default, marked blocked


async def test_reliability_weights_the_ranking(trust):
    c = trust
    await c.trust.refresh()
    feeds = {f["source_key"]: f for f in c.repo.all_feeds()}
    now = dt.datetime.now(UTC)
    for k, i in (("bbc", 1), ("hindu", 2)):
        c.ingest.process_items(feeds[k], [{"title": "Cabinet clears harbour dredging contract worth crores", "url": f"https://x.example/a{i}", "lead": "", "published": now}])
    for k, i in (("dtnext", 1), ("ndtv", 2)):
        c.ingest.process_items(feeds[k], [{"title": "Council postpones parking levy decision after protest", "url": f"https://x.example/b{i}", "lead": "", "published": now}])
    order = [s["title"][:10] for s in c.stories.list("world", "", "top")["stories"]]
    assert order[0] == "Cabinet cl"                                      # two outlets either way; the rated pair counts for more than the unrated pair
    cov = {r["title"][:10]: r["coverage"] for r in c.repo.q("SELECT title, coverage FROM stories")}
    assert cov["Cabinet cl"] > cov["Council po"]


def test_override_and_trust_api(trust, client):
    c = trust
    assert client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H).status_code == 200
    r = client.post("/api/jobs/refresh-trust", headers=H).json()
    import time
    for _ in range(100):
        j = client.get(f"/api/jobs/{r['job_id']}").json()
        if j["state"] == "done":
            break
        time.sleep(0.1)
    assert j["state"] == "done" and j["result"]["bands"]["reliable"] >= 2
    d = client.get("/api/sources/hindu").json()
    assert d["source"]["band"] == "reliable" and {s["signal"] for s in d["signals"]} >= {"lin2023", "wikipedia_rsp"}
    assert all(s["label"] and s["help"] for s in d["signals"]) and d["history"][0]["change"].startswith("unrated to reliable")
    m = client.get("/api/trust/method").json()
    assert m["floor"] == "all" and m["thresholds"]["reliable"] == 0.65 and {s["key"] for s in m["signals"]} >= {"lin2023", "wikipedia_rsp"} and m["last_run"]
    assert client.put("/api/sources/hindu/override", json={"band": "bogus"}, headers=H).status_code == 422
    assert client.put("/api/sources/hindu/override", json={"band": "blocked"}, headers=H).status_code == 200
    row = next(s for s in client.get("/api/sources?limit=500").json()["sources"] if s["key"] == "hindu")
    assert row["band"] == "blocked" and row["visible"] is True and row["override_band"] == "blocked"      # shown, marked blocked
    client.put("/api/sources/hindu/override", json={"band": None}, headers=H)
    assert next(s for s in client.get("/api/sources?limit=500").json()["sources"] if s["key"] == "hindu")["band"] == "reliable"
    # changing the floor in Settings rescored everything at once
    client.put("/api/settings", json={"changes": {"sources.floor": "highly_reliable"}}, headers=H)
    assert next(s for s in client.get("/api/sources?limit=500").json()["sources"] if s["key"] == "hindu")["visible"] is False
    client.post("/api/settings/reset", json={"key": "sources.floor"}, headers=H)
    assert next(s for s in client.get("/api/sources?limit=500").json()["sources"] if s["key"] == "hindu")["visible"] is True       # back to showing everything


def test_corroboration_counts_pickups_from_stored_stories(container):
    c = container
    feeds = {f["source_key"]: f for f in c.repo.all_feeds()}
    old = dt.datetime.now(UTC) - dt.timedelta(hours=40)
    c.repo.ensure_partition(old.date())
    headlines = ["Harbour authority signs dredging deal with consortium", "Monsoon session opens with walkout over fuel prices",
                 "Teachers union threatens strike before board exams", "Coastal erosion study finds shrinking beaches near estuary",
                 "Metro extension tender draws three bidders", "Fishermen stranded after trawler engine failure",
                 "Hospital launches robotic knee replacement programme", "Temple festival crowd management plan approved",
                 "Textile exporters seek relief on cotton duty", "Women's cricket academy opens in district headquarters",
                 "Heritage bridge closed for structural repairs", "Water board plans desalination plant tender"]
    for i, t in enumerate(headlines):
        c.ingest.process_items(feeds["hindu"], [{"title": t, "url": f"https://x.example/h{i}", "lead": "", "published": old}])
        if i < 6:                                                     # half of them were also reported by two other outlets
            for k in ("mint", "bbc"):
                c.ingest.process_items(feeds[k], [{"title": t + " says report", "url": f"https://x.example/{k}{i}", "lead": "", "published": old + dt.timedelta(minutes=30)}])
    c.repo.run("UPDATE stories SET first_seen = now() - interval '40 hours'")
    assert c.repo.corroboration_stats(24)["hindu"] == (12, 6)
    assert c.repo.corroboration_stats(72) == {}                      # nothing is old enough yet for a 72 hour judgement


def test_recompute_coverage_does_not_wait_for_rows_ingest_is_writing(container):
    """A held row lock (ingest mid-transaction) must not block or deadlock the batch; the next pass handles it."""
    import psycopg
    c = container
    feeds = {f["source_key"]: f for f in c.repo.all_feeds()}
    now = dt.datetime.now(UTC)
    c.ingest.process_items(feeds["hindu"], [{"title": "Lock test headline about quarry blasting rules", "url": "https://x.example/l1", "lead": "", "published": now}])
    c.repo.run("UPDATE sources SET visible = false WHERE key = 'hindu'")
    with psycopg.connect(c.config.database_url) as other:
        other.execute("SELECT id FROM stories FOR UPDATE")                    # ingest is mid-write on every story
        assert c.repo.recompute_coverage(7) == 0                                # returns at once, nothing changed
    assert c.repo.recompute_coverage(7) == 1
    assert c.repo.one("SELECT outlets FROM stories")["outlets"] == 0


async def test_partial_crawl_rates_only_new_sources_without_redownloading_datasets(trust):
    c = trust
    await c.trust.refresh()
    before = {s["key"]: s["trust_updated_at"] for s in c.repo.list_sources()}
    c.repo.candidate_add({"key": "newsite", "name": "New Site", "domain": "thehindu.com", "country": "IN", "type": "news", "language": "en", "origin": "mediacloud", "mc_id": 5,
                          "homepage": None, "media_type": None, "stories_per_week": 9.0, "note": "t"})
    c.repo.run("UPDATE sources SET status = 'active' WHERE key = 'newsite'")
    c.trust.fetcher.calls.clear()
    res = await c.trust.refresh(partial=True)
    assert res["sources"] == 1
    assert lin2023.URL not in c.trust.fetcher.calls and wikipedia_rsp.API not in c.trust.fetcher.calls
    after = {s["key"]: s["trust_updated_at"] for s in c.repo.list_sources()}
    assert after["newsite"] is not None and all(after[k] == before[k] for k in before)
    assert src(c, "newsite")["band"] == "reliable"                          # same domain as The Hindu: matched from the stored datasets
    assert c.repo.kv_get("trust_dirty") is False


async def test_dataset_download_is_retried_after_a_timeout(trust):
    c = trust
    c.trust.retry_pause = 0
    real = c.trust.fetcher.get
    seen = {"n": 0}

    async def flaky(url, *a, **k):
        from app.dao.fetch.http import FetchResult
        if url == wikipedia_rsp.API and seen["n"] < 2:
            seen["n"] += 1
            return FetchResult(None, error="Timed out")
        return await real(url, *a, **k)

    c.trust.fetcher.get = flaky
    res = await c.trust.refresh()
    assert seen["n"] == 2 and not any("Wikipedia" in n for n in res["notes"])
    assert src(c, "hindu")["band"] == "reliable" and src(c, "dailymail")["band"] == "blocked"      # the Wikipedia evidence arrived
