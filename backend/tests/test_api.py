import os
import datetime as dt

import pytest

from tests.conftest import FakeFetcher, rss

pytestmark = pytest.mark.db
H = {"X-Requested-With": "news-anchor"}


def login(client):
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
    assert client.post("/api/auth/setup", json={"password": "short"}, headers=H).status_code == 422
    assert client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H).status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_auth_and_csrf(client):
    assert client.get("/api/stories").status_code == 401
    login(client)
    assert client.get("/api/stories").status_code == 200
    assert client.put("/api/settings", json={"changes": {"feed.top_n": 5}}).status_code == 403          # no CSRF header
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/stories").status_code == 401
    assert client.post("/api/auth/login", json={"password": "wrong password"}, headers=H).status_code == 401
    assert client.post("/api/auth/login", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H).status_code == 200
    assert client.post("/api/auth/setup", json={"password": "another one 1"}, headers=H).status_code == 409


def test_settings_roundtrip_with_reset(client):
    login(client)
    s = client.get("/api/settings").json()
    assert "Storage" in s["groups"] and any(x["key"] == "feed.top_n" and x["is_default"] for x in s["settings"])
    assert client.put("/api/settings", json={"changes": {"feed.top_n": 5}}, headers=H).status_code == 200
    assert client.put("/api/settings", json={"changes": {"feed.top_n": 0}}, headers=H).status_code == 422
    val = lambda: next(x for x in client.get("/api/settings").json()["settings"] if x["key"] == "feed.top_n")
    assert val()["value"] == 5 and not val()["is_default"]
    client.post("/api/settings/reset", json={"key": "feed.top_n"}, headers=H)
    assert val()["value"] == 10 and val()["is_default"]


def test_topic_picker_and_menus(client):
    login(client)
    topics = {t["code"]: t for t in client.get("/api/topics").json()["topics"]}
    mr = topics["sport.motor-racing"]
    assert mr["enabled"] and mr["visible"] and {"Formula 1 (official)", "Autosport", "RaceFans"} <= set(mr["sources"])
    assert not topics["sport.gaelic"]["enabled"] and topics["sport"]["enabled"]
    menu = client.get("/api/topics/menu", params={"parent": "sport"}).json()
    names = [c["name"] for c in menu["children"]]
    assert "Motor racing" in names and "Cricket" in names and "Gaelic games" not in names and menu["hidden_count"] > 5
    assert client.put("/api/topics/sport.gaelic", json={"enabled": True}, headers=H).status_code == 200
    assert "Gaelic games" in [c["name"] for c in client.get("/api/topics/menu", params={"parent": "sport"}).json()["children"]]
    assert client.put("/api/topics/sport", json={"enabled": False}, headers=H).status_code == 404       # categories are always on
    client.post("/api/topics/reset", headers=H)
    assert "Gaelic games" not in [c["name"] for c in client.get("/api/topics/menu", params={"parent": "sport"}).json()["children"]]
    pm = client.get("/api/places/menu", params={"place": "IN"}).json()
    assert [b["name"] for b in pm["breadcrumb"]] == ["Global", "Asia", "South Asia", "India"] and any(k["code"] == "IN-TN" for k in pm["children"])
    world = client.get("/api/places/menu", params={"place": "world"}).json()
    assert [k["name"] for k in world["children"]] == ["Africa", "Americas", "Asia", "Europe", "Oceania"]
    assert {"European Union", "NATO", "ASEAN", "African Union", "Middle East"} <= {g["name"] for g in world["groups"]}
    eu = client.get("/api/places/menu", params={"place": "eu"}).json()
    assert len(eu["children"]) == 27 and all(k["level"] == "country" for k in eu["children"])
    assert client.get("/api/stories", params={"place": "eu", "tab": "top", "view": "ongoing"}).status_code == 200
    assert client.get("/api/stories", params={"place": "Narnia"}).status_code == 404
    idx = client.get("/api/index").json()
    assert any(p["code"] == "IN-TN-chennai" for p in idx["places"]) and any(t["code"] == "sport.chess" for t in idx["topics"])


def test_no_default_favourites_and_adding_one(client):
    login(client)
    assert client.get("/api/favourites").json()["favourites"] == []              # nothing is chosen for you
    assert next(s for s in client.get("/api/settings").json()["settings"] if s["key"] == "home.landing")["value"] == "world"
    client.post("/api/favourites", json={"place": "world", "topic": "sport.chess"}, headers=H)
    assert "Chess" in [f["label"] for f in client.get("/api/favourites").json()["favourites"]]
    client.delete("/api/favourites", params={"place": "world", "topic": "sport.chess"}, headers=H)
    assert "Chess" not in [f["label"] for f in client.get("/api/favourites").json()["favourites"]]


def test_refresh_job_end_to_end(client, container):
    login(client)
    now = dt.datetime.now(dt.timezone.utc)
    container.ingest.fetcher = FakeFetcher({"https://www.thehindu.com/news/national/feeder/default.rss": rss([
        {"title": "Union cabinet approves new scheme for farmers", "link": "https://www.thehindu.com/z1", "desc": "<p>Details</p>", "pub": now}])})
    container.settings.set_many({"search.embeddings": "hashed"})          # no model download in a test of the refresh job
    r = client.post("/api/jobs/refresh-news", headers=H).json()
    assert r["job_id"]
    import time
    for _ in range(300):
        j = client.get(f"/api/jobs/{r['job_id']}").json()
        if j["state"] == "done":
            break
        time.sleep(0.1)
    assert j["state"] == "done" and j["result"]["articles_new"] == 1
    st = client.get("/api/stories").json()["stories"]
    assert st[0]["title"].startswith("Union cabinet") and st[0]["url"] == "https://www.thehindu.com/z1"      # N1: a link to the publisher
    d = client.get(f"/api/stories/{st[0]['id']}").json()
    assert d["articles_list"][0]["source"]["provisional"] is True and d["articles_list"][0]["url"].startswith("https://www.thehindu.com/")
    status = client.get("/api/status").json()
    assert status["counts"]["stories"] == 1 and status["counts"]["feeds"] > 150
    assert client.put(f"/api/stories/{st[0]['id']}/saved", json={"saved": True}, headers=H).status_code == 200
    assert len(client.get("/api/stories", params={"tab": "saved"}).json()["stories"]) == 1
    assert client.get("/api/storage").json()["disk_total"] > 0 and "app_mb" in client.get("/api/resources").json()
