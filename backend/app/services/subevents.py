"""The distinct things said inside one story: when a story has absorbed an event of its own ("Trump says Tim Cook should run Nvidia" inside the story of
the Nvidia chief's resignation), this finds it from the story's headlines, so that the story page can list it.

A story's own theme words (used by most of its reports: nvidia, huang, ceo) say nothing about which part of the story a report is. The words that do are
the ones that several outlets use and most reports do not: "tim", "cook", "apple" for one part, "colette", "kress", "interim" for another. Words used by
the same reports are one sub-event; its size is the number of outlets that used them."""
from __future__ import annotations

import datetime as dt
import math
import re

from app.services.anchors import GENERIC
from app.services.textutil import title_key

_NUM = re.compile(r"\d[\d,.]*\d|\d")
MIN_OUTLETS, SHARE, THEME_SHARE, WHOLE_SHARE = 3, 0.03, 0.4, 0.7


def words_of(title: str, common: frozenset[str] | set[str] = frozenset()) -> set[str]:
    out = {w for w in title_key(title, limit=30) if w not in GENERIC and w not in common and not w.isdigit()}
    for n in _NUM.findall(title or ""):
        n = n.replace(",", "").strip(".")
        if n and not (len(n) == 4 and n.isdigit() and 1990 <= int(n) <= 2040):
            out.add("#" + n)
    return out


def find_subevents(reports: list[dict], common: frozenset[str] | set[str] = frozenset(), *, max_events: int = 8) -> list[dict]:
    """``reports``: dicts with ``title``, ``published`` (datetime), ``source`` and optionally ``url``. Returns the sub-events, biggest first:
    {"label", "headline", "url", "source", "outlets", "reports", "first", "last"}."""
    n = len(reports)
    sources = {r["source"] for r in reports}
    if n < 6 or len(sources) < 2 * MIN_OUTLETS:
        return []
    words = [r.get("words") or words_of(r["title"], common) for r in reports]
    need = max(MIN_OUTLETS, math.ceil(SHARE * len(sources)))
    used: dict[str, set[int]] = {}
    for i, ws in enumerate(words):
        for w in ws:
            used.setdefault(w, set()).add(i)
    terms = {}
    for w, idx in used.items():
        if len(idx) * 1.0 > THEME_SHARE * n:                                  # the story's own theme: says nothing about a part of it
            continue
        outlets = {reports[i]["source"] for i in idx}
        if len(outlets) >= need:
            terms[w] = (idx, outlets)
    clusters: list[dict] = []
    for w, (idx, outlets) in sorted(terms.items(), key=lambda kv: (-len(kv[1][1]), kv[0])):
        for c in clusters:
            inter = len(idx & c["seed"])
            if inter / max(1, len(idx | c["seed"])) >= 0.4:
                c["terms"].append(w)
                c["idx"] |= idx
                break
        else:
            clusters.append({"terms": [w], "seed": set(idx), "idx": set(idx)})
    events = []
    for c in clusters:
        idx = c["idx"]
        if len(c["terms"]) >= 2:                                              # keep the reports that use at least two of its words
            idx = {i for i in idx if len(words[i] & set(c["terms"])) >= 2} or idx
        outlets = {reports[i]["source"] for i in idx}
        if len(outlets) < need or len(idx) > WHOLE_SHARE * n or all(x.startswith("#") for x in c["terms"]):      # numbers alone are not a part of a story
            continue
        best = max(idx, key=lambda i: (len(words[i] & set(c["terms"])), -reports[i]["published"].timestamp()))
        ts = [reports[i]["published"] for i in idx]
        events.append({"label": ", ".join(t.lstrip("#") for t in c["terms"][:3]), "headline": reports[best]["title"], "url": reports[best].get("url"),
                       "source": reports[best]["source"], "outlets": len(outlets), "reports": len(idx), "first": min(ts), "last": max(ts), "_idx": idx})
    events.sort(key=lambda e: -e["outlets"])
    out: list[dict] = []
    for e in events:                                                          # one sub-event told twice (its words split in two clusters) is one
        if any(len(e["_idx"] & o["_idx"]) / max(1, len(e["_idx"] | o["_idx"])) >= 0.6 for o in out):
            continue
        out.append(e)
    for e in out:
        e.pop("_idx")
    return out[:max_events]
