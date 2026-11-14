"""Burdisso et al. 2024, "Reliability Estimation of News Media Sources: Birds of a Feather Flock Together" (NAACL 2024, Idiap; Apache-2.0).

Two files from github.com/idiap/News-Media-Reliability:
  reliability_scores.json   an estimate for 15,425 news domains, from the hyperlink graph of Common Crawl News (2019-2022): a site's
                            reliability is learned from the sites it links to and that link to it, seeded with journalists' labels.
                            Reported Spearman 0.80 against those labels. Values -1 (unreliable) to 1 (reliable); "p+fp-average" is the
                            variant the authors recommend.
  golden_truth_dataset.csv  the labels themselves for 5,332 domains: reliable (1), mixed (0), unreliable (-1) or deprecated (-2, Wikipedia), from Media Bias/Fact
                            Check, NewsGuard (with its 0-100 score when there is one) and Wikipedia's lists."""
from __future__ import annotations

import csv
import io
import json

SCORES_URL = "https://raw.githubusercontent.com/idiap/News-Media-Reliability/main/CC-NEWS/ALL/08/graph/en/reliability_scores.json"
LABELS_URL = "https://raw.githubusercontent.com/idiap/News-Media-Reliability/main/data/golden_truth/golden_truth_dataset.csv"
PAGE = "https://github.com/idiap/News-Media-Reliability"


def parse_scores(body: bytes) -> dict[str, float]:
    """domain -> estimate in -1..1."""
    out = {}
    for d, v in json.loads(body).items():
        x = v.get("p+fp-average")
        if isinstance(x, (int, float)):
            out[d.lower().removeprefix("www.")] = max(-1.0, min(1.0, float(x)))
    return out


def parse_labels(body: bytes) -> dict[str, tuple[int, float | None, str]]:
    """domain -> (label -1/0/1, NewsGuard score 0-100 or None, label source)."""
    out = {}
    for r in csv.DictReader(io.StringIO(body.decode("utf-8", "replace"))):
        try:
            lab = int(float(r.get("reliability_label") or ""))
        except ValueError:
            continue
        ng = r.get("newsguard_score") or ""
        try:
            ngv = float(ng) if ng else None
        except ValueError:
            ngv = None
        out[(r.get("domain") or "").lower().removeprefix("www.")] = (lab, ngv, r.get("label_source") or "")
    return out
