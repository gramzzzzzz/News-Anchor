"""Sources without a feed: search them all again, clear them, and the limit on discovered sources counts only the live ones."""
import asyncio

import pytest

pytestmark = pytest.mark.db


def add(c, key, domain, status="no_feed", curated=False):
    c.repo.candidate_add({"key": key, "name": key, "domain": domain, "country": "US", "type": "news", "language": "en", "origin": "mediacloud",
                          "mc_id": None, "homepage": f"https://{domain}/", "media_type": None, "stories_per_week": 5, "note": ""})
    c.repo.run("UPDATE sources SET status = %s, curated = %s, feed_search_at = now(), feed_search_tries = 1 WHERE key = %s", (status, curated, key))


def test_requeue_makes_no_feed_sources_due_but_does_not_count_toward_the_limit(container):
    c = container
    add(c, "a", "a.example")
    add(c, "b", "b.example")
    add(c, "live", "live.example", status="active")
    assert c.repo.candidates_pending() == 0                          # searched just now: next try in 30 days
    assert c.repo.no_feed_requeue() == 2
    assert c.repo.candidates_pending() == 2
    assert {r["key"] for r in c.repo.candidates_for_feed_search(10)} == {"a", "b"}
    assert {r["status"] for r in c.repo.q("SELECT status FROM sources WHERE key IN ('a','b')")} == {"no_feed"}
    assert c.discovery._room_left() == c.settings.get("discovery.max_total") - 1       # only the live source counts


def test_clear_deletes_no_feed_sources_remembers_their_sites_and_keeps_the_rest(container):
    c = container
    add(c, "a", "a.example")
    add(c, "b", "b.example")
    add(c, "live", "live.example", status="active")
    add(c, "starter", "starter.example", curated=True)
    c.repo.run("INSERT INTO source_history (source_key, change, reason) VALUES ('a', 'x', 'y')")
    assert c.repo.no_feed_clear() == 2
    keys = {r["key"] for r in c.repo.q("SELECT key FROM sources")}
    assert "a" not in keys and "b" not in keys and "live" in keys and "starter" in keys
    known = c.repo.known_domains()
    assert "a.example" in known and known["a.example"] == ""         # remembered, so discovery does not add it again
    assert c.repo.q("SELECT 1 FROM source_history WHERE source_key = 'a'") == []
    add_fn = asyncio.run(c.discovery._adder())[0]
    assert add_fn({"name": "A", "domain": "a.example", "country": "US", "type": "news", "language": "en", "origin": "web", "mc_id": None,
                   "homepage": "https://a.example/", "media_type": None, "stories_per_week": 1, "note": ""}) is None
