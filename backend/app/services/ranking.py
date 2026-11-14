"""How the Top and Trending lists are ordered (playbook 20.27). The database gives each candidate story its numbers (repo.list_stories); this module turns
them into an order, so that the rules are plain code that a replay (scripts/replay_lists.py) runs too.

Fresh      stories whose event was first reported in the last 24 hours (a new story, or a new development of an old storyline: stories are developments).
Ongoing    stories that began earlier and are still being reported in the last 24 hours.
Aggregated both, so that neither a giant ongoing story hides the fresh news nor the fresh news hides the giants. In Top the two lists are fused by rank
           (reciprocal rank fusion: each story scores 1 / (k + its place in its own list), k = 3); a giant's coverage and a new story's are not on one scale, which is
           why ranks are fused and not scores. In Trending a surge is on one scale, so the stories are sorted.

Top        a story's score is its fair coverage in the last 24 hours with decay: an outlet counts for (1 + age / T)^-g, T = 6 hours, g = 2, by when it joined.
           A story reported by 100 outlets in the last hour outranks one that had them all yesterday; a story with its coverage 12 hours ago counts for 1/9.
Trending   the surge: (outlets that joined in the last 2 hours - their usual number per 2 hours, read from the day before) / sqrt(usual + 1), at least 3
           outlets. A story that began in the window has no usual number, so all of its outlets count. The same 100 outlets an hour for two days is not a surge.
In every list stories with enough outlets come before the rest. A Breaking mark (many outlets within minutes of the first report) is shown on the row but does not rank: five outlets within half an hour is a low bar, and small stories pinned above the biggest news were worse than the rare late breaking story."""
from __future__ import annotations

import math
from dataclasses import dataclass

FUSION_K = 3


@dataclass
class Params:
    decay_h: float = 6.0
    gravity: float = 2.0
    fresh_h: float = 24.0
    trending_h: float = 2.0
    base_h: float = 24.0
    trending_min: int = 3
    active_h: float = 6.0
    fresh_soft: float = 0.0        # the same for a story first reported within the day (0: off)
    active_soft: float = 12.0       # 0: off. Otherwise an ongoing story counts by recent / (recent + active_soft), recent = outlets that joined in active_h hours

    @classmethod
    def from_settings(cls, s) -> "Params":
        return cls(decay_h=float(s.get("feed.decay_hours")), gravity=float(s.get("feed.decay_gravity")), fresh_h=float(s.get("feed.fresh_hours")),
                   trending_h=float(s.get("feed.trending_hours")), base_h=float(s.get("feed.trending_baseline_hours")),
                   trending_min=int(s.get("feed.trending_min_outlets")), active_h=float(s.get("feed.active_hours")), active_soft=float(s.get("feed.active_scale")))


def kernel(age_h: float, decay_h: float, gravity: float) -> float:
    """Weight of an outlet that joined ``age_h`` hours ago: 1 now, falling by a power law."""
    return (1.0 + max(age_h, 0.0) / decay_h) ** -gravity


def surge(row: dict) -> float:
    n, base = float(row.get("arrivals_now") or 0), float(row.get("arrivals_base") or 0)
    return max(0.0, (n - base) / math.sqrt(base + 1.0))


def activity(row: dict, p: Params) -> float:
    """How active an ongoing story still is, from 0 (no outlet joined it lately) to 1: recent / (recent + c). 1 when the rule is off."""
    c = p.fresh_soft if row.get("bucket") == "fresh" else p.active_soft
    if c <= 0:
        return 1.0
    n = float(row.get("recent_n") or 0)
    return n / (n + c)


def rank_rows(rows: list[dict], tab: str, p: Params, min_outlets: int) -> list[dict]:
    """Sets ``rank`` on each row and returns the rows in order: enough outlets first, then rank, then total coverage."""
    out = []
    for r in rows:
        if tab == "trending":
            if int(r.get("arrivals_now") or 0) < p.trending_min:
                continue
            r["rank"] = surge(r)
        else:
            r["rank"] = float(r.get("score") or 0)
            r["act"] = activity(r, p)
            r["rank"] *= r["act"]
        r["surge"] = surge(r) if tab == "trending" else None
        out.append(r)
    out.sort(key=lambda r: (1 if (tab == "trending" or int(r.get("indep_p") or 0) >= min_outlets) else 0, r["rank"], float(r.get("fair") or 0)), reverse=True)
    return out


def fuse(fresh: list[dict], ongoing: list[dict], k: int = FUSION_K) -> list[dict]:
    """Reciprocal rank fusion of the two lists (each in its own order): the first of each list share the top, then the seconds, and so on."""
    scored = []
    for lst in (fresh, ongoing):
        for i, r in enumerate(lst):
            scored.append((r.get("act", 1.0) / (k + i + 1), r["rank"], r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r for _s, _k, r in scored]


def order(rows: list[dict], tab: str, view: str, p: Params, min_outlets: int) -> list[dict]:
    """The final order of one list from its candidate rows (fresh and ongoing together; ``bucket`` says which a row is).
    Top, Aggregated: the two lists are fused by rank. Trending, Aggregated: a surge is on one scale for every story, so the stories are simply sorted."""
    fresh = rank_rows([r for r in rows if r["bucket"] == "fresh"], tab, p, min_outlets)
    ongoing = rank_rows([r for r in rows if r["bucket"] == "ongoing"], tab, p, min_outlets)
    if view == "fresh":
        return fresh
    if view == "ongoing":
        return ongoing
    if tab == "trending":
        return sorted(fresh + ongoing, key=lambda r: (r["rank"], float(r.get("fair") or 0)), reverse=True)
    return fuse(fresh, ongoing)
