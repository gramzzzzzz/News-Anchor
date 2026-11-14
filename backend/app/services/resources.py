"""Memory awareness. Reads the server's memory from /proc/meminfo, this process from /proc/self/status and
the database container's figure from a file its entrypoint refreshes. Keeps the app inside the owner's
RAM budget by lowering parallelism, never by crashing."""
from __future__ import annotations

import collections
import time
from pathlib import Path


def _meminfo() -> dict[str, int]:
    out = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) // 1024      # MB
    except (OSError, ValueError):
        pass
    return out


def process_rss_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    try:                                    # macOS dev fallback
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    except Exception:
        return 0.0


class ResourceGovernor:
    def __init__(self, settings, shared_dir: Path):
        self.settings = settings
        self.shared = shared_dir
        self._samples: collections.deque = collections.deque(maxlen=2880)   # 24 h at 30 s

    def db_mb(self) -> float | None:
        try:
            f = self.shared / "db_mem_kb"
            if time.time() - f.stat().st_mtime > 180:
                return None
            return int(f.read_text().strip()) / 1024
        except (OSError, ValueError):
            return None

    def sample(self) -> None:
        self._samples.append((time.time(), process_rss_mb()))

    def snapshot(self) -> dict:
        mi = _meminfo()
        app = process_rss_mb()
        peak = max([v for t, v in self._samples if t > time.time() - 86400] + [app])
        return {"server_total_mb": mi.get("MemTotal"), "server_available_mb": mi.get("MemAvailable"), "app_mb": round(app, 1),
                "db_mb": None if self.db_mb() is None else round(self.db_mb(), 1), "app_peak_24h_mb": round(peak, 1),
                "budget_mb": self.settings.get("resources.ram_budget_mb"), "leave_available_mb": self.settings.get("resources.leave_available_mb")}

    def concurrency(self) -> int:
        base = int(self.settings.get("refresh.concurrency"))
        budget = self.settings.get("resources.ram_budget_mb")
        app = process_rss_mb()
        avail = _meminfo().get("MemAvailable")
        c = base
        if app > 1.25 * budget:
            c = 1
        elif app > budget:
            c = max(1, base // 2)
        if avail is not None and avail < self.settings.get("resources.leave_available_mb"):
            c = max(1, min(c, base // 2))
        return max(1, c)
