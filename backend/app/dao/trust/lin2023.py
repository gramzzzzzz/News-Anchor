"""Lin et al. 2023, 'High level of correspondence across different news domain quality rating sets' (PNAS Nexus).
github.com/hauselin/domain-quality-ratings.

Their aggregate (pc1, 0 lowest to 1 highest) is the first principal component of six rating sets after imputing the ratings a set
does not have. The public file holds five of the six (NewsGuard's are licensed) and, for 6,105 of its 11,520 domains, none of the
five public sets actually rated the site: those pc1 values are imputed (or rest on NewsGuard alone). We therefore read the per-rater
file and count, for each domain, the public sets that really rated it, so the score can be trusted in proportion (TrustService).

How a real rating is told from an imputed one: published ratings are short decimals (MBFC 0.8333, Ad Fontes 0.9626), the Lewandowsky
list uses steps of 1/8, and MisinfoMe is a list, so only 0 (listed) is a rating; imputed values are long floats."""
from __future__ import annotations

import csv
import io

URL = "https://raw.githubusercontent.com/hauselin/domain-quality-ratings/main/data/domain_ratings.csv"
PAGE = "https://github.com/hauselin/domain-quality-ratings"
RATERS = {"afm": "Ad Fontes Media", "fc": "fact-checkers", "mbfc": "Media Bias/Fact Check", "lewandowsky_mean": "Lewandowsky et al. list",
          "misinfome_bin": "MisinfoMe list"}


def observed(col: str, v: str) -> bool:
    if v in ("", None):
        return False
    if col == "misinfome_bin":
        return v == "0"
    if col == "lewandowsky_mean":
        try:
            return abs(float(v) * 8 - round(float(v) * 8)) < 1e-9
        except ValueError:
            return False
    parts = v.split(".")
    return len(parts) == 1 or len(parts[1]) <= 4


def parse_full(text: str) -> dict[str, tuple[float, list[str]]]:
    """domain -> (pc1, the public rating sets that really rated it)."""
    out: dict[str, tuple[float, list[str]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        d = (row.get("domain") or "").strip().lower()
        if not d or "/" in d or d in out:                  # path entries (a section of a larger site) are not whole publishers
            continue
        try:
            pc1 = max(0.0, min(1.0, float(row["pc1"])))
        except (KeyError, ValueError):
            continue
        out[d] = (pc1, [RATERS[c] for c in RATERS if observed(c, row.get(c, ""))])
    return out


def parse(text: str) -> dict[str, float]:
    """domain -> pc1 (either file: the per-rater one or domain_pc1.csv)."""
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        d = (row.get("domain") or "").strip().lower()
        if not d or "/" in d or d in out:
            continue
        try:
            out[d] = max(0.0, min(1.0, float(row["pc1"])))
        except (KeyError, ValueError):
            continue
    return out


def coverage_factor(n_raters: int) -> float:
    """How much of the score to trust: an aggregate with no real public rating behind it is mostly imputation."""
    return {0: 0.25, 1: 0.7}.get(n_raters, 1.0)
