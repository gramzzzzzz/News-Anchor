"""Keeping storylines coherent.

A storyline (thread) is a chain of developments about one matter. Joining storylines by single links is how one of them grew to 5,389
developments (Hormuz, Sensex, weather forecasts and earnings calls together, 8 Nov 2026): one loose pair moved a whole storyline
into another, and the next loose pair did it again. Two rules stop that, in the way news-stream clustering systems keep clusters
compact (Miranda et al. 2018, Staykovski et al. 2019: a report joins a cluster by its similarity to the cluster's centroid, not to one
member; Liu et al. 2017, Story Forest: a story tree grows only by events that share its keywords):

* ThreadGuard: two storylines (or a development and a storyline) join only if their centroids are close AND the joined storyline stays
  cohesive: its developments' mean similarity to their common centre stays at least the setting storylines.cohesion (default COHESION_MIN), whatever its length. For unit
  vectors that mean similarity is |sum of the vectors| / n (the mean resultant length), so it is kept as running sums and costs
  nothing to check. A long storyline stays long as long as it stays about one thing; it stops growing the moment a join would blur it.
* split(): re-cluster a storyline's developments by average linkage with name evidence, to break up one that has already drifted.
  The repair job and the merge pass (for storylines it finds incoherent) use it."""
from __future__ import annotations

import numpy as np

from app.services.anchors import different_events, related

THREAD_COS = 0.60            # centroids of two storylines must be at least this similar to join
COHESION_MIN = 0.65          # default of the setting storylines.cohesion: the mean similarity of a storyline's developments to their centre; a join never takes it below, a storyline found below it is re-split
SPLIT_MIN = 8                # smaller storylines are not re-split
PAIR_COS = 0.65              # developments of one storyline: pairs this similar, naming something in common, are linked
GROUP_COS = 0.60             # ... and two groups join only if their centroids are still at least this similar


class ThreadGuard:
    """Decides joins between storylines during one merge pass, keeping running centroids so that a chain of individually acceptable joins
    cannot add up to an incoherent storyline. ``info(ids)`` gives {storyline: (mean vector, developments)} from the database."""

    def __init__(self, info, cohesion: float = COHESION_MIN):
        self._info_fn = info
        self.cohesion = cohesion
        self._sum: dict[int, np.ndarray] = {}
        self._n: dict[int, int] = {}
        self._parent: dict[int, int] = {}
        self._asked: set[int] = set()

    def preload(self, ids) -> None:
        need = [t for t in set(ids) if t not in self._asked]
        self._asked.update(need)
        for t, (mean, n) in self._info_fn(need).items():
            v = np.asarray(mean, dtype=np.float32)
            self._sum[t], self._n[t] = v * n, n

    def _find(self, t: int) -> int:
        while self._parent.get(t, t) != t:
            self._parent[t] = self._parent.get(self._parent[t], self._parent[t])
            t = self._parent[t]
        return t

    def ok(self, a: int, b: int, va: np.ndarray, vb: np.ndarray) -> bool:
        """May storylines a and b join? ``va``/``vb``: the vector of the development that links them (used for a storyline with no stored centroid)."""
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return True
        self.preload([ra, rb])
        sa, na = self._sum.get(ra, va.copy()), self._n.get(ra, 1)
        sb, nb = self._sum.get(rb, vb.copy()), self._n.get(rb, 1)
        if float(np.linalg.norm(sa + sb)) / (na + nb) < self.cohesion:          # the joined storyline would be less cohesive than allowed
            return False
        ca, cb = sa / np.linalg.norm(sa), sb / np.linalg.norm(sb)
        return float(ca @ cb) >= THREAD_COS

    def join(self, a: int, b: int, va: np.ndarray, vb: np.ndarray) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return
        sa, na = self._sum.get(ra, va.copy()), self._n.get(ra, 1)
        sb, nb = self._sum.get(rb, vb.copy()), self._n.get(rb, 1)
        self._parent[rb] = ra
        self._sum[ra], self._n[ra] = sa + sb, na + nb


def split(titles_names, toks, E: np.ndarray, frequent: set[str], cohesion: float = COHESION_MIN) -> list[list[int]]:
    """Index groups of a storyline's developments, each coherent. ``titles_names[i]``: the names in development i's headline;
    ``toks[i]``: its headline words; ``E``: unit vectors. Pairs at least PAIR_COS similar that name something in common (and are not
    different events) are linked strongest first; two groups join only while their centroids stay at least GROUP_COS similar
    (average linkage, so a chain of near neighbours cannot connect unrelated ends) and the joined group's mean similarity to its centre
    stays at least ``cohesion``, so a group cannot grow past the point where it stops being about one thing."""
    n = len(toks)
    edges = []
    for start in range(0, n, 256):
        S = E[start:start + 256] @ E.T
        for k in range(S.shape[0]):
            i = start + k
            S[k, i] = -1.0
            cand = np.where(S[k] >= PAIR_COS)[0]
            if len(cand) > 12:
                cand = cand[np.argsort(-S[k, cand])[:12]]
            for j in cand:
                j = int(j)
                if j > i and related(titles_names[i], titles_names[j], toks[i], toks[j], frequent) \
                        and not different_events(titles_names[i], titles_names[j], toks[i], toks[j], frequent):
                    edges.append((float(S[k, j]), i, j))
    parent = list(range(n))
    sums = {i: E[i].copy() for i in range(n)}
    size = {i: 1 for i in range(n)}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for _c, i, j in sorted(edges, reverse=True):
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        ci, cj = sums[ri] / size[ri], sums[rj] / size[rj]
        if float(ci @ cj) / max(1e-9, float(np.linalg.norm(ci) * np.linalg.norm(cj))) < GROUP_COS:
            continue
        if float(np.linalg.norm(sums[ri] + sums[rj])) / (size[ri] + size[rj]) < cohesion:      # the joined group must stay cohesive
            continue
        parent[rj] = ri
        sums[ri] = sums[ri] + sums[rj]
        size[ri] += size[rj]
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def repair_thread(repo, gaz, thread_id: int, frequent: set[str], *, apply: bool = True, cohesion: float = COHESION_MIN) -> dict:
    """Re-split one storyline into coherent ones. Returns {"before", "groups", "sizes"}; with ``apply`` the database is changed
    (each group becomes a storyline named by its earliest development; developments without a vector become their own)."""
    from app.services.anchors import anchors
    from app.services.textutil import title_key
    rows = repo.thread_members(thread_id)
    everyone = set(repo.thread_ids_all(thread_id))
    if len(rows) < 2:
        return {"before": len(everyone), "groups": 1, "sizes": [len(everyone)]}
    E = np.vstack([np.fromstring(r["e"][1:-1], sep=",", dtype=np.float32) for r in rows])
    E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    names = [anchors(r["title"], gaz) for r in rows]
    toks = [set(r["tokens"] or []) or set(title_key(r["title"])) for r in rows]
    idx_groups = split(names, toks, E, frequent, cohesion)
    groups = [[rows[i]["id"] for i in g] for g in idx_groups]
    loose = sorted(everyone - {i for g in groups for i in g})
    sizes = sorted((len(g) for g in groups), reverse=True) + [1] * len(loose)
    if apply and len(groups) + len(loose) > 1:
        repo.threads_split_apply(thread_id, groups, loose)
    return {"before": len(everyone), "groups": len(groups) + len(loose), "sizes": sizes}
