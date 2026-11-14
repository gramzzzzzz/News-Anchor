"""A small model that estimates, for a source without an expert rating, how likely the experts would be to call it Reliable.

Trained each day on the sources that do have an expert rating (about 1,000), from evidence measured for every source: the hyperlink-
graph estimate (Burdisso et al. 2024), rated outlets citing it (TrustRank's one step), sister outlets and pickup by rated outlets. This is the approach of Baly et al. 2018 ("Predicting factuality of reporting and bias of news
media sources", EMNLP): learn the experts' labels from features any site has, then predict for the sites they never reviewed.

Logistic regression (L2) for the probability, ridge regression for a score on the expert scale, both with numpy. Five-fold
cross-validation gives out-of-fold probabilities for the rated sources; the cut for an estimated Reliable is the lowest one at which at
least the chosen share (setting, 0.85) of the rated sources above it are rated Reliable or better, and likewise for an estimated Mixed.
Between the two cuts the model abstains and the source stays Unrated."""
from __future__ import annotations

import math

# Only evidence about the site's place among news outlets. Transparency pages and domain age are left out on purpose: the sites experts
# rate are mostly large outlets with full "about" pages and old domains, so a model would learn to mark down small local outlets for
# looking small (tried 2 Nov 2026: every estimate came out Mixed). They stay on the source card as before.
FEATURES = ("webgraph", "endorsement", "family", "corroboration")
NETWORK = ("webgraph", "endorsement", "family")         # an estimate needs at least one of these: pickups alone prove too little
NAMES = ["bias"] + [f"{k}_{x}" for k in FEATURES for x in ("has", "value")] + ["endorsement_count", "corroboration_count", "webgraph_top"]


def features(sigs: dict[str, tuple[float, float]]) -> list[float]:
    """sigs: signal -> (value, sample factor). Missing evidence is a 0 with its 'has' flag off, not a guess."""
    row = [1.0]
    for k in FEATURES:
        if k in sigs:
            row += [1.0, float(sigs[k][0]) - 0.5]
        else:
            row += [0.0, 0.0]
    row.append(float(sigs["endorsement"][1]) if "endorsement" in sigs else 0.0)
    row.append(float(sigs["corroboration"][1]) if "corroboration" in sigs else 0.0)
    row.append(1.0 if "webgraph" in sigs and sigs["webgraph"][0] >= 0.9 else 0.0)      # the estimate is most telling at its top (playbook 5.9)
    return row


def fit_logistic(X, y, lam: float = 1.0, iters: int = 30):
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    w = np.zeros(X.shape[1])
    reg = lam * np.eye(X.shape[1])
    reg[0, 0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + reg @ w
        H = (X * (p * (1 - p))[:, None]).T @ X + reg + 1e-6 * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        w -= step
        if float(np.abs(step).max()) < 1e-6:
            break
    return w


def fit_ridge(X, y, lam: float = 1.0):
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    reg = lam * np.eye(X.shape[1])
    reg[0, 0] = 0.0
    return np.linalg.solve(X.T @ X + reg, X.T @ y)


def prob(w, x) -> float:
    z = sum(a * b for a, b in zip(w, x))
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def out_of_fold(X, y, folds: int = 5) -> list[float]:
    import numpy as np
    n = len(y)
    idx = list(range(n))
    oof = [0.5] * n
    for f in range(folds):
        test = [i for i in idx if i % folds == f]
        train = [i for i in idx if i % folds != f]
        w = fit_logistic([X[i] for i in train], [y[i] for i in train])
        for i in test:
            oof[i] = prob(w, X[i])
    return oof


def cuts(p: list[float], y: list[int], want_hi: float, want_lo: float, min_support: int = 30) -> dict:
    """The probability above which at least ``want_hi`` of rated sources are Reliable, and below which at least ``want_lo`` are not."""
    pairs = sorted(zip(p, y), reverse=True)
    hi = None
    pos = 0
    for k, (pk, yk) in enumerate(pairs, 1):
        pos += yk
        if k >= min_support and pos / k >= want_hi:
            hi = (pk, k, pos / k)                       # keep the lowest cut that still meets the precision
    lo = None
    neg = 0
    below = [q for q in pairs if hi is None or q[0] < hi[0]]          # the two bands never overlap: Mixed only below the Reliable cut
    for k, (pk, yk) in enumerate(reversed(below), 1):
        neg += 1 - yk
        if k >= min_support and neg / k >= want_lo:
            lo = (pk, k, neg / k)
    auc = None
    P = [a for a, b in zip(p, y) if b == 1]
    N = [a for a, b in zip(p, y) if b == 0]
    if P and N:
        # rank-based AUC
        order = sorted(range(len(p)), key=lambda i: p[i])
        ranks = [0.0] * len(p)
        for r, i in enumerate(order, 1):
            ranks[i] = r
        sp = sum(ranks[i] for i in range(len(p)) if y[i] == 1)
        auc = (sp - len(P) * (len(P) + 1) / 2) / (len(P) * len(N))
    return {"hi": hi[0] if hi else None, "hi_support": hi[1] if hi else 0, "hi_precision": round(hi[2], 3) if hi else None,
            "lo": lo[0] if lo else None, "lo_support": lo[1] if lo else 0, "lo_precision": round(lo[2], 3) if lo else None,
            "auc": round(auc, 3) if auc is not None else None, "rated": len(y), "base_rate": round(sum(y) / len(y), 3) if y else None}
