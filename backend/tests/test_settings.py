import pytest

from app.services.settings_service import SettingError, SettingsService


def test_defaults_validation_and_reset(fake_repo):
    s = SettingsService(fake_repo)
    assert s.get("feed.top_n") == 10 and s.get("home.city") == "IN-TN-chennai" and s.get("storage.disk_cap_gb") is None
    s.set_many({"feed.top_n": "25", "feed.density": "compact"})
    assert s.get("feed.top_n") == 25 and fake_repo.s == {"feed.top_n": 25, "feed.density": "compact"}
    s.set_many({"feed.top_n": 10})                       # setting back to the default stores nothing
    assert "feed.top_n" not in fake_repo.s
    for bad in ({"feed.top_n": 0}, {"feed.top_n": 1000}, {"feed.density": "huge"}, {"nope": 1}, {"sources.exclude_types": ["blog"]}, {"feed.half_life_hours": None}):
        with pytest.raises(SettingError):
            s.set_many(bad)
    s.set_many({"storage.disk_cap_gb": 50})
    s.set_many({"storage.disk_cap_gb": None})            # nullable: back to "proposed"
    assert s.get("storage.disk_cap_gb") is None


def test_reset_single_group_all(fake_repo):
    s = SettingsService(fake_repo)
    s.set_many({"feed.top_n": 30, "feed.window_hours": 12, "refresh.concurrency": 4})
    assert s.reset(key="feed.top_n") == 1 and s.get("feed.top_n") == 10 and s.get("feed.window_hours") == 12
    s.reset(group="Feed")
    assert s.get("feed.window_hours") == 24 and s.get("refresh.concurrency") == 4
    s.reset()
    assert fake_repo.s == {}
    with pytest.raises(SettingError):
        s.reset(key="nope")


def test_profile_and_snapshot_flags(fake_repo):
    s = SettingsService(fake_repo)
    s.apply_profile("balanced")
    assert s.get("refresh.concurrency") == 16 and s.get("resources.ram_budget_mb") == 2048
    snap = {x["key"]: x for x in s.snapshot()}
    assert snap["refresh.concurrency"]["is_default"] is False and snap["feed.top_n"]["is_default"] is True
    assert all(x["help"] and x["label"] for x in snap.values())
