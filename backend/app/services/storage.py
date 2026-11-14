"""Disk accounting and enforcement (the owner's disk cap, free-space floor and retention)."""
from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)
GB = 1024 ** 3
UTC = dt.timezone.utc


class StorageGuard:
    def __init__(self, repo, settings, data_dir: Path):
        self.repo, self.settings, self.data_dir = repo, settings, data_dir

    def disk(self) -> tuple[int, int]:
        u = shutil.disk_usage(self.data_dir)
        return u.total, u.free

    def proposed_cap_gb(self) -> float:
        total, _ = self.disk()
        return round(min(120.0, max(1.0, total * 0.25 / GB)), 1)

    def cap_bytes(self) -> int:
        v = self.settings.get("storage.disk_cap_gb")
        return int((v if v is not None else self.proposed_cap_gb()) * GB)

    def usage(self) -> dict:
        db = self.repo.db_bytes()
        wal = self.repo.wal_bytes()
        reserve = int(self.settings.get("storage.image_reserve_gb") * GB)
        files = sum(f.stat().st_size for f in self.data_dir.rglob("*") if f.is_file()) if self.data_dir.exists() else 0
        # pg_database_size already includes tables and indexes; WAL lives outside the database files
        return {"database": db, "wal": wal or 0, "wal_known": wal is not None, "images_allowance": reserve, "files": files,
                "total": db + (wal or 0) + reserve + files}

    def status(self) -> dict:
        total, free = self.disk()
        u = self.usage()
        parts = self.repo.partitions()
        with_data = [p for p in parts if (p["approx_rows"] or 0) > 0 or p["bytes"] > 100_000]
        art_bytes = sum(p["bytes"] for p in parts)
        other = sum(self.repo.table_bytes().values())
        days = max(1, len(with_data))
        per_day = (art_bytes + other) / days
        cap = self.cap_bytes()
        retention = int(self.settings.get("storage.retention_days"))
        fixed = u["images_allowance"] + u["files"] + u["wal"]
        return {"disk_total": total, "disk_free": free, "usage": u, "cap": cap, "cap_confirmed": self.settings.get("storage.disk_cap_gb") is not None,
                "proposed_cap_gb": self.proposed_cap_gb(), "min_free": int(self.settings.get("storage.min_free_gb") * GB),
                "days_stored": len(parts), "days_with_data": len(with_data), "per_day_bytes": int(per_day), "fixed_bytes": int(fixed),
                "retention_days": retention, "paused": bool(self.repo.kv_get("ingest_paused", False)),
                "kept_days_note": self.repo.kv_get("kept_days_note"), "oldest_day": parts[0]["day"].isoformat() if parts else None,
                "tables": self.repo.table_bytes(), "estimate_basis_days": len(with_data)}

    def ensure_partitions(self) -> None:
        today = dt.datetime.now(UTC).date()
        for i in range(0, 3):
            self.repo.ensure_partition(today + dt.timedelta(days=i))

    def purge_wait_hours(self) -> float:
        """How long a one-outlet story waits for a second outlet: the setting, but never less than the age limit of feed items plus 12 hours
        (until then a feed may still list the item, and deleting the story would only store it again)."""
        return float(max(int(self.settings.get("storage.purge_wait_hours")), int(self.settings.get("refresh.max_item_age_hours")) + 12))

    def purge_uncorroborated(self, budget_s: float = 45.0) -> list[str]:
        """Delete the stories (and their articles) that no second outlet picked up. The counts are kept for the Status page (kv ``purge_stats``)."""
        res = self.repo.purge_uncorroborated(self.purge_wait_hours(), budget_s=budget_s)
        st = self.repo.kv_get("purge_stats") or {}
        now = dt.datetime.now(UTC)
        today = now.date().isoformat()
        per_day = dict(st.get("per_day") or {})
        if res["stories"]:
            d = per_day.get(today) or {"stories": 0, "articles": 0, "ads": 0}
            per_day[today] = {k: d.get(k, 0) + res[k] for k in ("stories", "articles", "ads")}
        st.update({"stories": int(st.get("stories", 0)) + res["stories"], "articles": int(st.get("articles", 0)) + res["articles"],
                   "ads": int(st.get("ads", 0)) + res["ads"], "last_at": now.isoformat(), "last": res,
                   "per_day": dict(sorted(per_day.items())[-14:]), "since": st.get("since") or now.isoformat()})
        self.repo.kv_set("purge_stats", st)
        return [f"Deleted {res['stories']} stories that only one outlet reported ({res['ads']} with an advertisement's headline) and their {res['articles']} articles"] if res["stories"] else []

    def enforce(self) -> list[str]:
        actions: list[str] = []
        today = dt.datetime.now(UTC).date()
        self.ensure_partitions()
        retention = int(self.settings.get("storage.retention_days"))
        for p in self.repo.partitions():
            if p["day"] < today - dt.timedelta(days=retention):
                self.repo.drop_partition(p["day"])
                actions.append(f"Dropped {p['day']} (older than {retention} days)")
        floor = int(self.settings.get("storage.min_free_gb") * GB)
        cap = self.cap_bytes()
        kept = None
        for _ in range(400):
            used = self.usage()["total"]
            _, free = self.disk()
            parts = [p for p in self.repo.partitions() if p["day"] < today]
            if not parts or (used <= 0.9 * cap and free >= floor + 2 * GB):
                break
            self.repo.drop_partition(parts[0]["day"])
            kept = len(self.repo.partitions())
            actions.append(f"Dropped {parts[0]['day']} to stay inside the disk cap or free-space floor")
        oldest = self.repo.partitions()
        if oldest:
            n = self.repo.delete_stories_before(dt.datetime.combine(oldest[0]["day"], dt.time.min, UTC))
            if n:
                actions.append(f"Removed {n} stories older than the oldest stored day")
            self.repo.reprints_prune(oldest[0]["day"])
        if self.settings.get("storage.purge_disabled_languages") and not self.settings.get("sources.all_languages"):
            res = self.repo.purge_languages(list(self.settings.get("sources.languages")))
            if res["articles"]:
                actions.append(f"Deleted {res['articles']} stored articles in languages you do not collect ({res['stories_deleted']} stories emptied)")
        if self.settings.get("storage.purge_uncorroborated") and not self.repo.kv_get("ingest_paused", False):
            actions.extend(self.purge_uncorroborated())
        used = self.usage()["total"]
        _, free = self.disk()
        paused = used >= cap or free < floor
        if paused != bool(self.repo.kv_get("ingest_paused", False)):
            actions.append("Paused saving new articles: the disk cap or free-space floor is reached" if paused else "Resumed saving new articles")
        self.repo.kv_set("ingest_paused", paused)
        if kept is not None and kept < retention:
            self.repo.kv_set("kept_days_note", f"Keeping {kept} of {retention} days because of the disk cap or free-space floor.")
        elif kept is None and not any("cap" in a for a in actions):
            self.repo.kv_set("kept_days_note", None)
        for a in actions:
            log.warning("StorageGuard: %s", a)
            self.repo.audit("storage", a)
        return actions
