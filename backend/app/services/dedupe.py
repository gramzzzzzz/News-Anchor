"""Near-duplicate suppression when a list is served (what news aggregators do as their last step, whatever the clustering before it did).

The grouping into stories is done in steps that can each leave two stories for one event (a story-length limit, a merge that did not run
yet, a headline worded alike but not enough). A list must still not show the same news twice, so rows are compared once the page is made:
a row is dropped when a higher ranked row is the same news, meaning
  - their meaning vectors are alike (cosine >= ``min_cos``), and
  - their reports overlap in time or follow each other within ``gap_h`` hours, and
  - they share a third of their rare headline words (rare = used by at most 3 of the rows compared), and
  - their headlines do not give different casualty counts or name different people, places and teams.
The dropped row is not lost: it is returned with the row that stands for it."""
from __future__ import annotations

import datetime as dt

import numpy as np

from app.services.anchors import GENERIC, anchors, conflict
from app.services.merge import casualty_clash
from app.services.textutil import title_key

MIN_COS, RARE_DF, MIN_RARE_OVERLAP = 0.82, 3, 0.34


def same_news(a: dict, b: dict, cos: float, rare_words: set[str], gap_h: float, min_cos: float = MIN_COS) -> bool:
    """``a`` and ``b`` carry ``title``, ``first_seen``, ``last_update``, ``words`` (headline words) and ``names`` (anchors of the headline)."""
    if cos < min_cos:
        return False
    gap = (max(a["first_seen"], b["first_seen"]) - min(a["last_update"], b["last_update"])) / dt.timedelta(hours=1)
    if gap > gap_h or casualty_clash(a["title"], b["title"]) or conflict(a["names"], b["names"]):
        return False                                       # not together in time, a different count, or each names things the other does not
    ra, rb = a["words"] & rare_words, b["words"] & rare_words
    return bool(ra and rb) and len(ra & rb) / min(len(ra), len(rb)) >= MIN_RARE_OVERLAP     # and they say the same rare things (alike meaning alone also fits two events of one subject)


def drop_duplicates(rows: list[dict], meta: dict[int, dict], gap_h: float, min_cos: float = MIN_COS) -> tuple[list[dict], dict[int, list[int]]]:
    """``rows`` in rank order; ``meta[id]`` has ``e`` (the vector text), ``first_seen``, ``last_update``, ``title``.
    Returns the rows without the duplicates and, for each kept row, the ids dropped for it."""
    ids = [r["id"] for r in rows if r["id"] in meta and meta[r["id"]].get("e")]
    if len(ids) < 2:
        return rows, {}
    E = np.array([np.fromstring(meta[i]["e"][1:-1], sep=",", dtype=np.float32) for i in ids])
    E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    S = E @ E.T
    pos = {i: k for k, i in enumerate(ids)}
    for i in ids:
        m = meta[i]
        m.setdefault("words", {w for w in title_key(m["title"]) if w not in GENERIC})
        m.setdefault("names", anchors(m["title"]))
    df: dict[str, int] = {}
    for i in ids:
        for w in meta[i]["words"]:
            df[w] = df.get(w, 0) + 1
    rare = {w for w, n in df.items() if n <= RARE_DF}
    kept: list[dict] = []
    folded: dict[int, list[int]] = {}
    for r in rows:
        i = r["id"]
        if i in pos:
            hit = next((k["id"] for k in kept if k["id"] in pos and same_news(meta[k["id"]], meta[i], float(S[pos[k["id"]], pos[i]]), rare, gap_h, min_cos)), None)
            if hit is not None:
                folded.setdefault(hit, []).append(i)
                continue
        kept.append(r)
    return kept, folded


def without_repeats(rows: list[dict], meta: dict[int, dict], gap_h: float, key) -> list[dict]:
    """The rows in the order ``key`` gives (best first) without the rows that repeat a better one."""
    kept, _folded = drop_duplicates(sorted(rows, key=key, reverse=True), meta, gap_h)
    return kept


def group_by_storyline(rows: list[dict], meta: dict[int, dict], min_cos: float = 0.65, eligible=lambda r: True, max_children: int = 8) -> list[list[dict]]:
    """Rows in list order, grouped: a row joins the first group of its storyline (thread) in which one row is at least ``min_cos`` alike in meaning.
    A storyline is wider than an event (the Ukraine storyline holds the Hormuz talks too, at 0.49), so the storyline alone does not group.
    The first row of a group is its best ranked; groups stay in the order of their first rows. Nothing is dropped: a row with no vector, or that is not
    ``eligible`` (too few outlets to be a development worth showing), stands alone; a group holds at most ``max_children`` besides its first."""
    vecs: dict[int, np.ndarray | None] = {}

    def vec(i: int):
        if i not in vecs:
            e = (meta.get(i) or {}).get("e")
            v = np.fromstring(e[1:-1], sep=",", dtype=np.float32) if e else None
            vecs[i] = v / max(float(np.linalg.norm(v)), 1e-9) if v is not None else None
        return vecs[i]
    groups: list[list[dict]] = []
    by_thread: dict[int, list[list[dict]]] = {}
    for r in rows:
        v = vec(r["id"])
        target = None
        if v is not None and eligible(r):
            for g in by_thread.get(r.get("thread_id"), []):
                if len(g) <= max_children and any(vec(m["id"]) is not None and float(v @ vec(m["id"])) >= min_cos for m in g):
                    target = g
                    break
        if target is None:
            target = [r]
            groups.append(target)
            if eligible(r):
                by_thread.setdefault(r.get("thread_id"), []).append(target)
        else:
            target.append(r)
    return groups
