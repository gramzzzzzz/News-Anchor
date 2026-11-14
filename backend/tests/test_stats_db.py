import os
import datetime as dt

import pytest

from tests.test_search_db import put_article

pytestmark = pytest.mark.db
H = {"X-Requested-With": "news-anchor"}


def test_stats_summarise_what_was_collected_and_when_things_last_ran(client, container):
    c = container
    put_article(c, "hindu", "Floods hit Chennai suburbs as lake overflows", "https://st/1", 2, topics=("disaster", "disaster.storms-floods"), places=("IN", "IN-TN"))
    put_article(c, "bbc", "Parliament debates data bill", "https://st/2", 30, topics=("politics",), places=("GB",))
    c.repo.kv_set("discovery_last_web", {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "queries": 30, "promoted": 5})
    c.repo.kv_set("trust_last_run", {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "sources": 160, "seconds": 12.0, "bands": {"reliable": 3}, "notes": []})
    client.post("/api/auth/setup", json={"password": os.environ["NA_TEST_PASSWORD"]}, headers=H)
    d = client.get("/api/stats").json()
    assert d["news"]["articles"] == 2 and d["news"]["articles_24h"] == 1 and d["news"]["stories"] == 2 and d["news"]["last_article_seen"]
    assert sum(x["n"] for x in d["news"]["per_day"]) == 2
    assert d["sources"]["total"] >= 159 and d["sources"]["by_status"]["active"] >= 159 and d["sources"]["by_origin"]["starter"] >= 159 and "IN" in d["sources"]["active_by_country"]
    assert d["feeds"]["total"] >= 200 and d["refresh"]["interval_minutes"] == 30
    cats = {x["code"]: x for x in d["categories"]["top"]}
    assert cats["disaster"]["stories"] == 1 and cats["politics"]["stories"] == 1 and len(cats) == 17
    assert any(x["name"] == "Storms, floods and droughts" and x["stories"] == 1 for x in d["categories"]["busiest_subtopics"])
    geo = d["geography"]
    assert geo["countries_total"] >= 240 and geo["countries_with_stories_7d"] == 2 and geo["countries_with_active_source"] >= 20 and geo["countries_without_source_count"] > 100
    assert d["discovery"]["last_web"]["queries"] == 30 and d["trust"]["last_run"]["sources"] == 160
    assert d["storage"]["database_bytes"] > 0 and isinstance(d["jobs"], list) and d["ai"]["ready"] is False


def test_change_password(client):
    client.post("/api/auth/setup", json={"password": "first password"}, headers=H)
    assert client.post("/api/auth/password", json={"current": "wrong", "new": "second password"}, headers=H).status_code == 403
    assert client.post("/api/auth/password", json={"current": "first password", "new": "short"}, headers=H).status_code == 422
    assert client.post("/api/auth/password", json={"current": "first password", "new": "second password"}, headers=H).status_code == 200
    client.post("/api/auth/logout", headers=H)
    assert client.post("/api/auth/login", json={"password": "first password"}, headers=H).status_code == 401
    assert client.post("/api/auth/login", json={"password": "second password"}, headers=H).status_code == 200
