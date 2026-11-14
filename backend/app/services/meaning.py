"""The meaning check on topic tags.

The keyword rules in topics.txt tag a story with a sub-topic when its headline uses one of the sub-topic's words. Words are ambiguous
("batter" in a housing story, "Derby" in a horse race, "Ai" in Vietnamese), and about a quarter of sub-topic tags were wrong on a
hand-checked sample. Each tag that the rules give is checked again with three signals that do not depend on the headline's words:

* Neighbours (the strongest; the idea of Bahri, Jiang and Gupta, "Deep k-NN for noisy labels", ICML 2020): the 25 stories of the last three days whose
  meaning vectors are closest to this one. A right tag is usually shared by some of them; a word collision is not. Measured as the lift
  over the topic's share of all stories, so a rare topic is not penalised for being rare.
* A per-topic model of the topic's meaning: logistic regression on the meaning vectors, trained daily on the last three days' keyword tags.
* The section in the article's address (/sport/cricket/, /business/markets/): a label set by an editor at the publisher (urlsection.py).

They are combined by a small logistic model fitted on 312 hand-checked tags (leave-one-out evaluation, weighted back to all tags):

  score = sigmoid(0.62 * logit(p_topic) + 0.75 * log((neighbours + 0.02) / (share + 0.02)) - 1.07 * section_contradicts
                  + 1.67 * section_supports - 1.11)

A tag scoring below ``feed.meaning_min`` (0.65) is marked rejected (kept in the table, not used); for topics with fewer than 300 stories
in three days the cut is 0.15 lower. A category tag is rejected only when every sub-topic of it the story holds was rejected and the
category's own score is below the cut too. Stories without a meaning vector are not judged, nor are topics without a model.

Measured (2 Nov 2026), after the keyword rules: about 68% of the wrong sub-topic tags are removed and 15% of the right ones; the share
of wrong tags among those kept falls from about 26% to about 12%. For rare topics, 24% of right tags are lost (67% of wrong ones)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
from collections import Counter

import numpy as np

from app.services import urlsection

log = logging.getLogger(__name__)
UTC = dt.timezone.utc

MIN_POSITIVES = 20          # fewer stories than this: no model, the tag is never judged
NEG_SAMPLE = 5000           # negatives per topic
WINDOW_DAYS = 3
WINDOW_MAX = 80000          # most recent stories in the reference set (about 120 MB of vectors)
ITERATIONS = 300
K = 25                      # neighbours
RARE_STORIES = 300
RARE_EASE = 0.15
COMBINE = (0.62, 0.75, -1.07, 1.67, -1.11)     # p_topic (logit), neighbour lift (log), section contradicts, section supports, bias
REF_MINUTES = 60            # the reference set is read again after this long


def _vec(text: str) -> np.ndarray:
    return np.fromstring(text[1:-1], sep=",", dtype=np.float32)


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def fit(X: np.ndarray, y: np.ndarray, it: int = ITERATIONS, lr: float = 2.0, lam: float = 1e-3) -> np.ndarray:
    """Class-balanced logistic regression by gradient descent. X has a bias column."""
    w = np.zeros(X.shape[1], np.float32)
    sw = np.where(y == 1, 0.5 / max(1.0, float(y.sum())), 0.5 / max(1.0, float((1 - y).sum()))).astype(np.float32)
    for _ in range(it):
        p = 1 / (1 + np.exp(-(X @ w)))
        w -= lr * (X.T @ (sw * (p - y)) + lam * w)
    return w


def score(p_topic: float, neighbours: float, share: float, contradicts: bool, supports: bool) -> float:
    a, b, c, d, e = COMBINE
    z = a * _logit(p_topic) + b * math.log((neighbours + 0.02) / (share + 0.02)) + c * contradicts + d * supports + e
    return 1 / (1 + math.exp(-z))


def judge(tags: list[str], fails: dict[str, bool]) -> set[str]:
    """The tags of one story to reject, from which judged tags fail the cut. A sub-topic is rejected when it fails; a category only when
    it fails and every sub-topic of it on the story was rejected (a category tag with no sub-topic is never rejected)."""
    out = {c for c in tags if "." in c and fails.get(c)}
    for c in tags:
        if "." in c or not fails.get(c):
            continue
        subs = [s for s in tags if s.startswith(c + ".")]
        if subs and all(s in out for s in subs):
            out.add(c)
    return out


class MeaningCheck:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings
        self._lock = asyncio.Lock()
        self._w: dict[str, np.ndarray] | None = None
        self._ref: dict | None = None

    # ---- the reference set: recent stories' vectors and tags ------------------------------------
    _TAGS_SQL = "ARRAY(SELECT code FROM story_topics t WHERE t.story_id = s.id AND t.n * 3 >= s.articles) AS topics"

    def _load(self, where: str, params: tuple, page: int = 5000, cap: int = WINDOW_MAX) -> tuple[list[int], np.ndarray, list[set[str]]]:
        """Stories with meaning vectors, newest first, read in pages (the vectors arrive as text, about 4 KB a story)."""
        ids, parts, tags, last = [], [], [], 2 ** 62
        while len(ids) < cap:
            rows = self.repo.q(f"""SELECT s.id, s.embedding::text AS e, {self._TAGS_SQL} FROM stories s
                                   WHERE s.id < %s AND s.embed_backend = 'model' AND s.embedding IS NOT NULL AND {where}
                                   ORDER BY s.id DESC LIMIT %s""", (last, *params, min(page, cap - len(ids))))
            if not rows:
                break
            E = np.vstack([_vec(r["e"]) for r in rows])
            E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
            parts.append(E)
            ids += [r["id"] for r in rows]
            tags += [set(r["topics"] or []) for r in rows]
            last = rows[-1]["id"]
        E = np.vstack(parts) if parts else np.zeros((0, 384), np.float32)
        return ids, E, tags

    def reference(self, fresh: bool = False) -> dict:
        now = dt.datetime.now(UTC)
        if fresh or not self._ref or now - self._ref["at"] > dt.timedelta(minutes=REF_MINUTES):
            self._ref = None                                     # let the old arrays go before reading new ones
            ids, E, tags = self._load("s.last_update > now() - make_interval(days => %s)", (WINDOW_DAYS,))
            self._ref = {"ids": ids, "pos": {i: k for k, i in enumerate(ids)}, "E": E, "tags": tags,
                         "count": Counter(t for ts in tags for t in ts), "at": now}
        return self._ref

    # ---- the topic models -------------------------------------------------------------------------
    def train(self, tag_version: str | None = None) -> dict:
        ref = self.reference(fresh=True)
        E, tags = ref["E"], ref["tags"]
        if len(E) < 1000:
            return {"skipped": f"only {len(E)} stories with meaning vectors"}
        codes: dict[str, list[int]] = {}
        for i, ts in enumerate(tags):
            for c in ts:
                codes.setdefault(c, []).append(i)
        rng = np.random.default_rng(0)
        weights = {}
        for c, pos in codes.items():
            if len(pos) < MIN_POSITIVES:
                continue
            pset = set(pos)
            neg = np.array([i for i in rng.choice(len(E), size=min(len(E), NEG_SAMPLE + len(pos)), replace=False) if i not in pset][:NEG_SAMPLE])
            idx = np.concatenate([np.array(pos), neg])
            X = np.hstack([E[idx], np.ones((len(idx), 1), np.float32)])
            y = np.concatenate([np.ones(len(pos), np.float32), np.zeros(len(neg), np.float32)])
            weights[c] = fit(X, y)
        self._w = weights
        at = dt.datetime.now(UTC).isoformat()
        self.repo.kv_set("meaning_weights", {"at": at, "w": {c: [round(float(v), 5) for v in w] for c, w in weights.items()}})
        self.repo.kv_set("meaning_meta", {"at": at, "tag": tag_version, "topics": len(weights), "stories": len(E)})
        log.info("Meaning check trained for %d topics on %d stories", len(weights), len(E))
        return {"topics": len(weights), "stories": len(E), "at": at}

    def weights(self) -> dict[str, np.ndarray]:
        if self._w is None:
            d = self.repo.kv_get("meaning_weights") or {}
            self._w = {c: np.array(v, np.float32) for c, v in (d.get("w") or {}).items()}
        return self._w

    # ---- checking ---------------------------------------------------------------------------------
    def apply(self, hours: float | None = None, batch: int = 256) -> dict:
        """Mark (and unmark) rejected tags on stories updated in the last ``hours`` (every story in the reference set when None)."""
        W = self.weights()
        on = bool(self.settings.get("feed.meaning_check"))
        cut = float(self.settings.get("feed.meaning_min"))
        if not W or not on:
            n = self.repo.run("UPDATE story_topics SET rejected = false WHERE rejected")
            return {"cleared": n}
        ref = self.reference()
        if len(ref["ids"]) < K + 1:
            return {"skipped": "too few stories"}
        if hours is None:
            t_ids, t_E, t_tags = ref["ids"], ref["E"], ref["tags"]
        else:
            t_ids, t_E, t_tags = self._load("s.last_update > now() - %s * interval '1 hour'", (float(hours),), cap=20000)
        n_ref = len(ref["ids"])
        E = ref["E"]
        judged = rejected = changed = 0
        for start in range(0, len(t_ids), batch):
            ids = t_ids[start:start + batch]
            X = t_E[start:start + batch]
            S = X @ E.T
            for k, sid in enumerate(ids):                        # a story is not its own neighbour
                j = ref["pos"].get(sid)
                if j is not None:
                    S[k, j] = -2.0
            nn = np.argpartition(-S, K, axis=1)[:, :K]
            del S
            urls: dict[int, list[list[str]]] = {}
            for r in self.repo.q("SELECT story_id, url FROM articles WHERE story_id = ANY(%s)", (ids,)):
                urls.setdefault(r["story_id"], []).append(urlsection.sections(r["url"]))
            want: list[tuple[int, str, bool]] = []
            for k, sid in enumerate(ids):
                tags = sorted(t_tags[start + k])
                neigh = [ref["tags"][j] for j in nn[k]]
                x = np.append(X[k], np.float32(1.0))
                fails: dict[str, bool] = {}
                for c in tags:
                    w = W.get(c)
                    if w is None:
                        continue
                    p = float(1 / (1 + np.exp(-(x @ w))))
                    share_n = sum(1 for ts in neigh if c in ts) / K
                    ev = [urlsection.evidence(c, secs) for secs in urls.get(sid, [])]
                    sup, con = any(e > 0 for e in ev), any(e < 0 for e in ev) and not any(e > 0 for e in ev)
                    m = score(p, share_n, ref["count"].get(c, 0) / n_ref, con, sup)
                    fails[c] = m < (cut - RARE_EASE if ref["count"].get(c, 0) < RARE_STORIES else cut)
                bad = judge(tags, fails)
                rejected += len(bad)
                want.extend((sid, c, c in bad) for c in tags)
            judged += len(ids)
            changed += self.repo.topic_rejections_set(list(ids), want)
        return {"stories": judged, "rejected": rejected, "changed": changed}

    async def run(self) -> dict:
        """After each refresh: train once a day and whenever stories were tagged again (then check every story), otherwise check the
        stories updated since the last check (at most every 10 minutes)."""
        if self._lock.locked():
            return {"skipped": "already running"}
        async with self._lock:
            tv = await asyncio.to_thread(self.repo.kv_get, "tag_version")
            key = f"v2|{tv}|{self.settings.get('feed.meaning_min')}|{self.settings.get('feed.meaning_check')}"
            d = await asyncio.to_thread(self.repo.kv_get, "meaning_meta") or {}
            stale = not d.get("at") or d.get("tag") != tv or dt.datetime.now(UTC) - dt.datetime.fromisoformat(d["at"]) > dt.timedelta(hours=24)
            out: dict = {}
            if stale and tv and self.settings.get("feed.meaning_check"):
                out["train"] = await asyncio.to_thread(self.train, tv)
            full = "topics" in out.get("train", {}) or (await asyncio.to_thread(self.repo.kv_get, "meaning_key")) != key
            last = await asyncio.to_thread(self.repo.kv_get, "meaning_last_check")
            now = dt.datetime.now(UTC)
            if not full and last and now - dt.datetime.fromisoformat(last) < dt.timedelta(minutes=10):
                return out or {"skipped": "checked less than 10 minutes ago"}
            hours = None if full else max(0.5, (now - dt.datetime.fromisoformat(last)).total_seconds() / 3600 + 0.25) if last else 6
            out["check"] = await asyncio.to_thread(self.apply, hours)
            await asyncio.to_thread(self.repo.kv_set, "meaning_last_check", now.isoformat())
            if full:
                await asyncio.to_thread(self.repo.kv_set, "meaning_key", key)
            return out
