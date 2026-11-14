"""Audit of the lists: does any list show the same news twice? (run: docker exec -e PYTHONPATH=/app news-anchor python /app/scripts/list_audit.py)

For every Top period and Trending, the first 40 rows are compared in pairs. A pair is reported when
  - the two rows are in one chain (should never be: the list shows a chain as one row), or
  - their meaning vectors are alike (cosine >= 0.80) and their reports overlap in time or follow within 8 hours, or
  - they share most of their rare headline words (overlap >= 0.6) and are alike in meaning (cosine >= 0.55), in the same time.
Exit code 1 when a pair is found."""
from __future__ import annotations

import sys

import numpy as np

from app.config import load_config
from app.container import build

c = build(load_config())
repo = c.repo
repo.open()
N = 40
bad = 0
for tab, name in (("top", "latest"), ("top", "6h"), ("top", "12h"), ("top", "24h"), ("top", "3d"), ("top", "7d"), ("trending", "trending")):
    rows = c.stories.list("world", "", tab, N, period=name if tab == "top" else "24h")["stories"]          # what a reader gets
    ids = [r["id"] for r in rows]
    meta = {r["id"]: r for r in repo.q("""SELECT id, chain_id, thread_id, tokens, first_seen, last_update, embedding::text AS e FROM stories WHERE id = ANY(%s)""", (ids,))}
    ids = [i for i in ids if meta[i]["e"]]                              # stories without a meaning vector cannot be compared
    rows = [r for r in rows if r["id"] in set(ids)]
    E = np.array([np.fromstring(meta[i]["e"][1:-1], sep=",", dtype=np.float32) for i in ids])
    E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
    S = E @ E.T
    df = {}
    for i in ids:
        for w in set(meta[i]["tokens"]):
            df[w] = df.get(w, 0) + 1
    found = []
    for a in range(len(ids)):
        for b in range(a):
            ma, mb = meta[ids[a]], meta[ids[b]]
            same_chain = (ma["chain_id"] or ids[a]) == (mb["chain_id"] or ids[b])
            gap = (max(ma["first_seen"], mb["first_seen"]) - min(ma["last_update"], mb["last_update"])).total_seconds() / 3600
            ta, tb = set(ma["tokens"]), set(mb["tokens"])
            rare = {w for w in ta | tb if df.get(w, 0) <= 3}
            ov = len((ta & tb) & rare) / max(1, min(len(ta & rare), len(tb & rare)))
            cos = float(S[a, b])
            why = "same chain" if same_chain else f"cos {cos:.2f}, gap {gap:.0f}h" if cos >= 0.80 and gap <= 8 else f"rare words {ov:.2f}, cos {cos:.2f}" if ov >= 0.6 and cos >= 0.55 and gap <= 8 else ""
            if why:
                found.append(f"   #{b + 1} {rows[b]['title'][:55]!r} ({ids[b]})  ~  #{a + 1} {rows[a]['title'][:55]!r} ({ids[a]}): {why}")
    print(f"{name:9s} {len(rows)} rows, {len(found)} suspected duplicates")
    print("\n".join(found))
    bad += len(found)
sys.exit(1 if bad else 0)
