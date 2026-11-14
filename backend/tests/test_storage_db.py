import datetime as dt

import pytest

pytestmark = pytest.mark.db
GB = 1024 ** 3
UTC = dt.timezone.utc


def test_retention_drops_whole_days(container):
    c = container
    today = dt.datetime.now(UTC).date()
    for d in (10, 40, 100, 120):
        c.repo.ensure_partition(today - dt.timedelta(days=d))
    c.settings.set_many({"storage.retention_days": 30})
    actions = c.storage.enforce()
    days = {p["day"] for p in c.repo.partitions()}
    assert today in days and today - dt.timedelta(days=10) in days
    assert today - dt.timedelta(days=40) not in days and today - dt.timedelta(days=120) not in days
    assert len([a for a in actions if "older than 30 days" in a]) == 3


def test_cap_pauses_ingestion_and_drops_oldest_first(container):
    c = container
    today = dt.datetime.now(UTC).date()
    for d in (1, 2, 3):
        c.repo.ensure_partition(today - dt.timedelta(days=d))
    c.settings.set_many({"storage.disk_cap_gb": 1, "storage.image_reserve_gb": 2})     # reserve alone exceeds the cap
    c.storage.enforce()
    assert c.repo.kv_get("ingest_paused") is True
    assert {p["day"] for p in c.repo.partitions()} >= {today}                          # today's partition is never dropped
    c.settings.set_many({"storage.disk_cap_gb": 500, "storage.min_free_gb": 0, "storage.image_reserve_gb": 0})
    c.storage.enforce()
    assert c.repo.kv_get("ingest_paused") is False


def test_status_reports_disk_and_estimates(container):
    s = container.storage.status()
    assert s["disk_total"] > s["disk_free"] > 0 and s["cap"] > 0 and not s["cap_confirmed"]
    assert s["proposed_cap_gb"] >= 1 and s["usage"]["total"] >= s["usage"]["database"]
    container.settings.set_many({"storage.disk_cap_gb": 12})
    s = container.storage.status()
    assert s["cap_confirmed"] and s["cap"] == 12 * GB


def test_interrupted_jobs_are_closed_on_start(container):
    repo = container.repo
    a, b = repo.job_create("refresh-news"), repo.job_create("refresh-trust")
    repo.job_update(b, state="done")
    assert repo.jobs_close_interrupted() >= 1
    assert repo.job_get(a)["state"] == "failed" and repo.job_get(b)["state"] == "done"


def test_old_publication_times_never_delete_stories_whose_articles_are_kept(container):
    import datetime as _dt
    from app.services.textutil import title_key
    c = container
    now = _dt.datetime.now(_dt.timezone.utc)
    c.repo.ensure_partition(now.date())
    with c.repo.pool.connection() as conn:
        a = {"seen_day": now.date(), "source_key": "hindu", "feed_id": None, "url": "https://o.example/1", "url_hash": "o1", "title": "Old feature on delta farming published last week",
             "lead": "", "published_at": now - _dt.timedelta(days=3), "seen_at": now, "country": "IN", "stype": "news"}
        sid, _ = c.repo.ingest_article(conn, a, None, {"title": a["title"], "lead": "", "tokens": title_key(a["title"])}, {}, {})
    c.storage.enforce()                                            # the oldest stored day is today; the story's time is three days ago
    assert c.repo.one("SELECT id FROM stories WHERE id = %s", (sid,))
    c.repo.run("DELETE FROM stories WHERE id = %s", (sid,))          # a story lost the old way comes back
    assert c.repo.stories_restore_orphans(title_key) == 1 and c.repo.one("SELECT articles FROM stories WHERE id = %s", (sid,))["articles"] == 1
