"""Evaluate the trust score against two independent expert verdicts. Run after trust_eval_collect.py:
    cd backend && .venv/bin/python ../scripts/trust_eval_analyse.py

Method: the app combines expert ratings (Lin et al. 2023 aggregate, Wikipedia's perennial-sources list) with weak signals
(transparency links, domain age, owner on record). We cannot test the combined score against the truth, which nobody has; we test
it against each expert while hiding that expert from the score ('leave one expert out'). If the remaining evidence still puts
domains on the right side of the other expert's verdict, the formula is sound. Harm is counted both ways:
  false high  a source the experts call unreliable that the score calls Reliable or better
  false low   a source the experts call reliable that the score calls Low (Mixed counts as a milder miss)"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np

OUT = Path("/tmp/na-eval")
sample = json.loads((OUT / "sample.json").read_text())
lin_all = json.loads((OUT / "lin.json").read_text())
rsp_all = json.loads((OUT / "rsp.json").read_text())
RSP_V = {"generally reliable": 0.85, "no consensus": 0.5, "generally unreliable": 0.15, "deprecated": 0.0, "blacklisted": 0.0}
RSP_RELIABLE = {"generally reliable"}
RSP_UNRELIABLE = {"generally unreliable", "deprecated", "blacklisted"}


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    p, n = np.array(pos), np.array(neg)
    return float(((p[:, None] > n[None, :]).sum() + 0.5 * (p[:, None] == n[None, :]).sum()) / (len(p) * len(n)))


# ------------------------------------------------------------------------------------------------ A
print("A. How well do the two expert sources agree? (domains in both:", len(set(lin_all) & set(rsp_all)), ")")
rel = [lin_all[d] for d, e in rsp_all.items() if d in lin_all and e["status"] in RSP_RELIABLE]
unr = [lin_all[d] for d, e in rsp_all.items() if d in lin_all and e["status"] in RSP_UNRELIABLE]
nc = [lin_all[d] for d, e in rsp_all.items() if d in lin_all and e["status"] == "no consensus"]
for name, v in (("Wikipedia generally reliable", rel), ("no consensus", nc), ("generally unreliable / deprecated", unr)):
    if v:
        print(f"   {name:36s} n={len(v):3d}  Lin mean {np.mean(v):.2f}  p10 {np.percentile(v, 10):.2f}  median {np.median(v):.2f}  p90 {np.percentile(v, 90):.2f}")
print(f"   AUC of Lin predicting Wikipedia's verdict: {auc(rel, unr):.3f}")
for cut in (0.5, 0.6, 0.65, 0.7, 0.75):
    print(f"   Lin >= {cut:.2f}: reliable kept {np.mean([x >= cut for x in rel]):.0%}, unreliable wrongly passed {np.mean([x >= cut for x in unr]):.0%}")

# ------------------------------------------------------------------------------------------------ B
rows = []
for d, r in sample.items():
    v = {"domain": d, "lin": lin_all.get(d), "rsp": rsp_all.get(d, {}).get("status")}
    v["tr"] = (0.5 + 0.5 * r["transparency"]) if r.get("fetched") else None
    v["age"] = min(0.9, 0.2 + 0.07 * r["age"]) if r.get("age") is not None else None
    v["owner"] = 0.75 if r.get("owner") else None
    v["wd"] = bool(r.get("wikidata"))
    rows.append(v)
print("\nB. Do the weak signals tell reliable from unreliable? (AUC: 0.5 = no information, 1 = perfect)")
for label, is_pos, is_neg in (
        ("vs Wikipedia verdict", lambda r: r["rsp"] in RSP_RELIABLE, lambda r: r["rsp"] in RSP_UNRELIABLE),
        ("vs Lin high (>=0.8) / low (<=0.3)", lambda r: r["lin"] is not None and r["lin"] >= 0.8, lambda r: r["lin"] is not None and r["lin"] <= 0.3)):
    print("  ", label)
    for sig, name in (("tr", "transparency (raise-only mapping)"), ("age", "domain age"), ("owner", "owner on record"), ("wd", "has a Wikidata entry")):
        pos = [float(r[sig] or 0) for r in rows if is_pos(r) and (sig in ("wd",) or r[sig] is not None or sig == "owner")]
        neg = [float(r[sig] or 0) for r in rows if is_neg(r) and (sig in ("wd",) or r[sig] is not None or sig == "owner")]
        a = auc(pos, neg)
        cov_p = np.mean([r[sig] is not None for r in rows if is_pos(r)]) if any(is_pos(r) for r in rows) else 0
        print(f"      {name:36s} AUC {a:.3f}   (n {len(pos)}+{len(neg)}; present for {cov_p:.0%} of the reliable group)" if a is not None else f"      {name}: not enough data")


# ------------------------------------------------------------------------------------------------ C
def score(sigs: list[tuple[float, float]], prior: float) -> tuple[float, float]:
    tw = sum(w for _, w in sigs)
    return (prior * 0.5 + sum(w * v for v, w in sigs)) / (prior + tw), tw / (tw + 2)


LOW_NEEDS_TWO = False


def band(sc: float, conf: float, has_expert: bool, th: tuple[float, float, float], n_experts: int = 1) -> str:
    if not has_expert:
        return "unrated"
    hi, re_, mi = th
    if sc >= hi and conf >= 0.5:
        return "highly"
    if sc >= re_:
        return "reliable"
    if sc >= mi or (LOW_NEEDS_TWO and n_experts < 2):
        return "mixed"          # one rating set alone never calls a source Low
    return "low"


def evaluate(hide: str, wexp: float, wweak: float, th, prior: float, target: str) -> dict:
    """hide: which expert is hidden from the score ('lin' or 'rsp'); the other one is the verdict we test against."""
    c = {"n_rel": 0, "n_unr": 0, "false_high": 0, "false_low": 0, "rel_mixed": 0, "unrated_rel": 0, "unrated_unr": 0, "unr_mixed_or_below": 0, "unr_mixed": 0}
    for r in rows:
        if hide == "lin":                       # test against Wikipedia
            truth = 1 if r["rsp"] in RSP_RELIABLE else 0 if r["rsp"] in RSP_UNRELIABLE else None
            expert = [(RSP_V[rsp_all[r["domain"]]["status"]], wexp)] if False else ([(r["lin"], wexp)] if r["lin"] is not None else [])
        else:                                   # hide Wikipedia, expert = Lin; truth = Lin extremes
            truth = 1 if (r["lin"] is not None and r["lin"] >= 0.8) else 0 if (r["lin"] is not None and r["lin"] <= 0.3) else None
            expert = [(RSP_V[r["rsp"]], wexp)] if r["rsp"] in RSP_V else []
        if truth is None:
            continue
        if hide == "lin" and r["lin"] is None:
            expert = []
        weak = [(r[k], wweak * wk) for k, wk in (("tr", 1.0), ("age", 0.5), ("owner", 0.5)) if r[k] is not None]
        sc, conf = score(expert + weak, prior)
        b = band(sc, conf, bool(expert), th, len(expert))
        if truth == 1:
            c["n_rel"] += 1
            c["false_low"] += b == "low"
            c["rel_mixed"] += b == "mixed"
            c["unrated_rel"] += b == "unrated"
        else:
            c["n_unr"] += 1
            c["false_high"] += b in ("reliable", "highly")
            c["unr_mixed_or_below"] += b in ("mixed", "low")
            c["unr_mixed"] += b == "mixed"
            c["unrated_unr"] += b == "unrated"
    return c


def harm(m: dict) -> float:
    """Weighted misses as a share of rated sources: a wrong 'reliable' on an unreliable source and a wrong 'low' on a reliable one count 3x a 'mixed'."""
    rated = (m["n_rel"] - m["unrated_rel"]) + (m["n_unr"] - m["unrated_unr"])
    return (3 * m["false_high"] + 3 * m["false_low"] + m["rel_mixed"] + 0.5 * m["unr_mixed"]) / max(rated, 1)


print("\nC. The score with one expert hidden, against the other expert's verdict")
CUR = dict(wexp=3.0, wweak=1.0, th=(0.80, 0.65, 0.45), prior=1.0)
for hide, name in (("lin", "Lin hidden? no: Wikipedia is hidden from nothing here"), ):
    pass
for hide, text in (("lin", "score uses Lin + weak signals; tested against Wikipedia's verdict"),
                   ("rsp", "score uses Wikipedia + weak signals; tested against Lin high/low")):
    m = evaluate(hide, **CUR, target="")
    print(f"   {text}")
    print(f"      reliable n={m['n_rel']}: called Low {m['false_low']}, Mixed {m['rel_mixed']}, unrated {m['unrated_rel']}"
          f"   |   unreliable n={m['n_unr']}: called Reliable+ {m['false_high']}, Mixed/Low {m['unr_mixed_or_below']}, unrated {m['unrated_unr']}   | harm {harm(m):.3f}")

print("\nD. What each ingredient adds (current thresholds), harm = lower is better")
for label, kw in (("current settings", {}), ("no weak signals", {"wweak": 0.0}), ("weak signals x2", {"wweak": 2.0}), ("weak signals x0.5", {"wweak": 0.5}),
                  ("expert weight 2", {"wexp": 2.0}), ("expert weight 5", {"wexp": 5.0}), ("prior weight 0.5", {"prior": 0.5}), ("prior weight 2", {"prior": 2.0})):
    cfg = {**CUR, **kw}
    h = [evaluate(x, **cfg, target="") for x in ("lin", "rsp")]
    print(f"   {label:20s} harm {harm(h[0]):.3f} / {harm(h[1]):.3f}   false high {h[0]['false_high']}/{h[0]['n_unr']}, {h[1]['false_high']}/{h[1]['n_unr']}   false low {h[0]['false_low']}/{h[0]['n_rel']}, {h[1]['false_low']}/{h[1]['n_rel']}")

print("\nE. Threshold search (weights as current). Best by combined harm; top 8")
res = []
for hi, re_, mi in itertools.product((0.74, 0.77, 0.80, 0.83), (0.55, 0.60, 0.65, 0.70), (0.35, 0.40, 0.45, 0.50)):
    if not (hi > re_ > mi):
        continue
    cfg = {**CUR, "th": (hi, re_, mi)}
    h = [evaluate(x, **cfg, target="") for x in ("lin", "rsp")]
    res.append((harm(h[0]) + harm(h[1]), (hi, re_, mi), h))
for tot, th, h in sorted(res, key=lambda x: x[0])[:8]:
    print(f"   highly {th[0]:.2f} reliable {th[1]:.2f} mixed {th[2]:.2f}   harm {tot:.3f}   false high {h[0]['false_high']}+{h[1]['false_high']}  false low {h[0]['false_low']}+{h[1]['false_low']}  reliable->Mixed {h[0]['rel_mixed']}+{h[1]['rel_mixed']}")
cur_tot = sum(harm(evaluate(x, **CUR, target="")) for x in ("lin", "rsp"))
print(f"   (current thresholds: harm {cur_tot:.3f})")

print("\nF. Known cases (score from Lin + weak signals, Wikipedia hidden) -- spot check")
for d in ("infowars.com", "naturalnews.com", "breitbart.com", "dailymail.co.uk", "rt.com", "sputniknews.com", "thegatewaypundit.com", "bbc.com", "reuters.com", "apnews.com", "nature.com", "thehindu.com"):
    r = next((x for x in rows if x["domain"] == d), None)
    lin = lin_all.get(d)
    if r is None and lin is None:
        continue
    r = r or {"tr": None, "age": None, "owner": None}
    ex = [(lin, 3.0)] if lin is not None else []
    weak = [(r[k], wk) for k, wk in (("tr", 1.0), ("age", 0.5), ("owner", 0.5)) if r.get(k) is not None]
    sc, cf = score(ex + weak, 1.0)
    print(f"   {d:24s} lin {lin if lin is None else round(lin, 2)}  rsp {rsp_all.get(d, {}).get('status', '-'):22s} -> {band(sc, cf, bool(ex), CUR['th'])} ({sc:.2f})")


print("\nG. Policy variants (all with Lin or Wikipedia alone as the only expert, the hardest case)")
import copy
for label, low2, wweak_age in (("as built", False, 0.5), ("one rating set never says Low", True, 0.5), ("... and domain age weight 0.25", True, 0.25)):
    LOW_NEEDS_TWO = low2
    rows_backup = rows
    for r in rows:
        r["_agew"] = wweak_age
    # domain-age weight is applied through the weak list below
    def weak_list(r, wweak):
        return [(r[k], wweak * wk) for k, wk in (("tr", 1.0), ("age", wweak_age), ("owner", 0.5)) if r[k] is not None]
    globals()["_weak"] = weak_list
    h = []
    for hide in ("lin", "rsp"):
        c = {"n_rel": 0, "n_unr": 0, "false_high": 0, "false_low": 0, "rel_mixed": 0, "unrated_rel": 0, "unrated_unr": 0, "unr_mixed_or_below": 0, "unr_mixed": 0}
        for r in rows:
            if hide == "lin":
                truth = 1 if r["rsp"] in RSP_RELIABLE else 0 if r["rsp"] in RSP_UNRELIABLE else None
                expert = [(r["lin"], 3.0)] if r["lin"] is not None else []
            else:
                truth = 1 if (r["lin"] is not None and r["lin"] >= 0.8) else 0 if (r["lin"] is not None and r["lin"] <= 0.3) else None
                expert = [(RSP_V[r["rsp"]], 3.0)] if r["rsp"] in RSP_V else []
            if truth is None:
                continue
            sc, conf = score(expert + weak_list(r, 1.0), 1.0)
            b = band(sc, conf, bool(expert), CUR["th"], len(expert))
            key = "n_rel" if truth == 1 else "n_unr"
            c[key] += 1
            if truth == 1:
                c["false_low"] += b == "low"; c["rel_mixed"] += b == "mixed"; c["unrated_rel"] += b == "unrated"
            else:
                c["false_high"] += b in ("reliable", "highly"); c["unr_mixed"] += b == "mixed"; c["unrated_unr"] += b == "unrated"
        h.append(c)
    print(f"   {label:34s} harm {harm(h[0]):.3f} / {harm(h[1]):.3f}   false high {h[0]['false_high']}+{h[1]['false_high']}   false low {h[0]['false_low']}+{h[1]['false_low']}   reliable->Mixed {h[0]['rel_mixed']}+{h[1]['rel_mixed']}   unreliable->Mixed {h[0]['unr_mixed']}+{h[1]['unr_mixed']}")
