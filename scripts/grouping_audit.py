"""Audit of how reports are grouped into stories (run inside the app container: docker exec -e PYTHONPATH=/app news-anchor python /app/scripts/grouping_audit.py).

Missed merges   pairs of stories the merge rule says are one event (decide() and not different_events()) that are still two stories
Late splits     the same, but the stories' first reports are more than the development window apart while the older one is still receiving reports
Mixed stories   stories whose reports do not belong together: the share of a story's report headlines far from the story's centre
Time outliers   stories whose first report is far earlier than the bulk of their reports"""
from __future__ import annotations

import datetime as dt
import math
import random
import sys
from collections import Counter

import numpy as np

from app.config import load_config
from app.container import build
from app.services.anchors import anchors, different_events, rarity_bar
from app.services.merge import T_LOW, decide, rare_overlap

c = build(load_config())
repo = c.repo
repo.open()
merger = c.merger
backend = repo.kv_get("embed_backend") or "model"
rows, vecs = [], []
_cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
for r in repo.window_rows(72, backend):
    if (r["outlets"] or 0) < 2 and r["first_seen"] < _cut:
        continue                                   # memory: older single-report stories are left out of this audit
    vecs.append(np.fromstring(r.pop("e")[1:-1], sep=",", dtype=np.float32))
    rows.append(r)
E = np.vstack(vecs)
E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
n = len(rows)
toks = [set(r["tokens"]) for r in rows]
df = Counter(w for s in toks for w in s)
idf = {w: math.log(n / (1 + x)) for w, x in df.items()}
frequent = {w for w, x in df.items() if x > rarity_bar(n)}
places = [merger._fine(set(r["places"] or [])) for r in rows]
anc = (lambda code: merger.gaz.ancestors(code)) if merger.gaz else (lambda code: [])
ids = [r["id"] for r in rows]
_x = {}
for k in range(0, len(ids), 5000):
    for r in repo.q("SELECT id, last_update, outlets, articles FROM stories WHERE id = ANY(%s)", (ids[k:k + 5000],)):
        _x[r["id"]] = r
last = {i: r["last_update"] for i, r in _x.items()}
size = {i: (r["outlets"], r["articles"]) for i, r in _x.items()}
now = dt.datetime.now(dt.timezone.utc)
from app.services.merge import BIG_STORY, same_event_in_time
GAP, SPAN = int(c.settings.get("feed.story_gap_hours")) * 3600, int(c.settings.get("feed.development_hours")) * 3600
names_cache: dict[int, set] = {}
def names(i):
    if i not in names_cache:
        names_cache[i] = anchors(rows[i]["title"], merger.gaz)
    return names_cache[i]

recent = [i for i, r in enumerate(rows) if now - r["first_seen"] < dt.timedelta(hours=12)]
cats: dict[str, list] = {"missed": [], "late": [], "other_day": []}
for i in recent:
    s = E[i] @ E.T
    s[i] = -1
    for j in np.where(s >= T_LOW)[0]:
        j = int(j)
        a, b = rows[i], rows[j]
        if a["thread_id"] == b["thread_id"] and False:
            pass
        rare = rare_overlap(toks[i], toks[j], idf)
        if not decide(float(s[j]), rare, a["title"], b["title"], places[i], places[j], anc):
            continue
        if different_events(names(i), names(j), toks[i], toks[j], frequent):
            continue
        if min(size[a["id"]][0], size[b["id"]][0]) < 1:
            continue
        if float(s[j]) < 0.80 and max(size[a["id"]][0], size[b["id"]][0]) >= BIG_STORY:
            continue                                       # the weak test does not join a big story (BIG_STORY outlets)
        older = a if a["first_seen"] <= b["first_seen"] else b
        key = "missed" if same_event_in_time(a, b, GAP, SPAN) else ("late" if now - last[older["id"]] < dt.timedelta(hours=6) else "other_day")
        cats[key].append((float(s[j]), a["id"], b["id"], a["title"], b["title"], size[a["id"]], size[b["id"]]))
print(f"stories in the window: {n}; created in the last 12 h: {len(recent)}")
for k, v in cats.items():
    uniq = {tuple(sorted((x[1], x[2]))): x for x in v}
    print(f"{k}: {len(uniq)} pairs")
random.seed(1)
for k in ("missed", "late"):
    uniq = list({tuple(sorted((x[1], x[2]))): x for x in cats[k]}.values())
    for x in sorted(uniq, key=lambda x: -(x[5][0] + x[6][0]))[:8]:
        print(f"  [{k}] cos {x[0]:.2f}  #{x[1]} ({x[5][0]} outlets) {x[3][:70]!r}  <->  #{x[2]} ({x[6][0]}) {x[4][:70]!r}")

# mixed stories: report headlines far from the centre of the story's own reports (needs the embedder, so only a sample)
print("\nmixed-story check")
big = [r for r in repo.q("""SELECT id, title, outlets, articles FROM stories WHERE last_update > now() - interval '48 hours' AND articles BETWEEN 12 AND 400 AND NOT routine
                             ORDER BY random() LIMIT 40""")]
import asyncio
async def mixed():
    out = []
    for s in big:
        arts = repo.q("SELECT title FROM articles WHERE story_id = %s ORDER BY published_at LIMIT 60", (s["id"],))
        titles = [a["title"] for a in arts]
        if len(titles) < 8:
            continue
        v, used = await c.embedder.embed(titles)
        V = np.array(v, dtype=np.float32); V /= np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-9)
        cen = V.mean(0); cen /= np.linalg.norm(cen)
        sim = V @ cen
        far = float((sim < 0.5).mean())
        out.append((far, s["id"], s["title"], len(titles), [t for t, x in zip(titles, sim) if x < 0.5][:3]))
    return out
res = asyncio.run(mixed())
res.sort(reverse=True)
print(f"checked {len(res)} stories; with >=20% of reports far from the centre: {sum(1 for r in res if r[0] >= 0.2)}")
for far, sid, t, k, ex in res[:6]:
    print(f"  #{sid} {t[:70]!r} ({k} reports, {far:.0%} far) e.g. {[e[:50] for e in ex]}")
