"""Turns trust evidence into a score, a confidence and a band. Pure functions, no I/O.

score      = (prior_weight * 0.5 + sum(w_i * v_i)) / (prior_weight + sum(w_i))     w_i = base weight * sample factor
confidence = sum(w_i) / (sum(w_i) + 2)       low < 0.5 <= medium < 0.75 <= high
A neutral prior keeps thin evidence from giving extreme scores. A band normally needs an expert signal. A source without one gets an
*estimated* band when enough evidence about it was measured (who cites it, the published hyperlink-graph estimate, its sister
outlets, pickup by rated outlets): never Highly reliable, Low only when that evidence is itself poor, always labelled estimated, and
never counted as a reliable outlet for a story's "Confirmed" tag. Otherwise it is 'unrated'."""
from __future__ import annotations

from dataclasses import dataclass

BAND_RANK = {"low": 0, "mixed": 1, "reliable": 2, "highly_reliable": 3}
BAND_LABEL = {"highly_reliable": "Highly reliable", "reliable": "Reliable", "mixed": "Mixed", "low": "Low", "unrated": "Unrated", "blocked": "Blocked"}
OVERRIDE_BANDS = ("highly_reliable", "reliable", "mixed", "low", "blocked")

SIGNALS: dict[str, dict] = {
    "lin2023": {"label": "Expert rating aggregate", "expert": True,
                "help": "Lin et al. 2023 (PNAS Nexus): one quality score per news domain, combining six expert rating sets. 0 is lowest, 1 highest."},
    "wikipedia_rsp": {"label": "Wikipedia perennial sources", "expert": True,
                      "help": "What Wikipedia's editors concluded after repeated discussion of the source: generally reliable, no consensus, generally unreliable, or deprecated."},
    "factcheck_labels": {"label": "Fact-checker labels", "expert": True,
                         "help": "Reliable, mixed or unreliable as labelled by Media Bias/Fact Check, NewsGuard (its 0 to 100 score when there is one) or Wikipedia, collected by Burdisso et al. 2024 for 5,332 domains. Used only where Lin et al. 2023 has no score (it already includes those raters)."},
    "webgraph": {"label": "Hyperlink-graph estimate", "expert": False, "estimate": True,
                 "help": "Burdisso et al. 2024 (NAACL): reliability learned from which news sites link to which, across four years of Common Crawl News, seeded with journalists' labels (Spearman 0.80 against them). 15,425 domains. An estimate, not a rating."},
    "endorsement": {"label": "Cited by rated outlets", "expert": False, "estimate": True,
                    "help": "Measured here: outlets with an expert rating that link to this site in their articles or name it as their source. The value is their average rating, each outlet counted once (TrustRank's idea: trust flows along the links of reviewed sites). Needs two or more such outlets; full weight from five."},
    "family": {"label": "Sister outlets", "expert": False, "estimate": True,
               "help": "The average rating of other outlets with the same owner on record (Wikidata), when two or more of them have an expert rating."},
    "estimate_model": {"label": "Estimate (model)", "expert": False,
                       "help": "For a source without an expert rating: the chance that experts would rate it Reliable or better, from the measured evidence, by a model trained daily on the sources experts did rate. An estimated band is given only above or below cuts at which the model is right for the chosen share of rated sources (cross-validated); in between the source stays Unrated."},
    "estimate_score": {"label": "Estimate (score)", "expert": False, "help": "The model's score on the expert scale, used for ranking a source without an expert rating."},
    "corroboration": {"label": "Picked up by rated outlets", "expert": False, "estimate": True,
                      "help": "Measured here. Share of the stories it reported first (at least a day old) that an outlet with an expert rating of Reliable or better also reported in its own words. It can raise a score; having few pickups is not held against local or niche outlets."},
    "transparency": {"label": "Transparency pages", "expert": False,
                     "help": "Whether the home page links to about, contact, ethics or editorial standards, corrections, masthead or team, and ownership pages. It can raise a score; a missing link is not held against a site, because the crawler cannot tell 'absent' from 'built in a way I cannot read'."},
    "domain_age": {"label": "Domain age", "expert": False, "help": "Years since the domain was registered (RDAP). A very new domain is a warning sign; an old one proves little."},
    "ownership": {"label": "Ownership on record", "expert": False, "help": "Wikidata records an owner or parent organisation."},
}
EXPERT = {k for k, v in SIGNALS.items() if v["expert"]}
ESTIMATE = {k for k, v in SIGNALS.items() if v.get("estimate")}


@dataclass
class Sig:
    signal: str
    value: float
    weight: float              # effective: base weight * sample factor
    detail: str = ""
    url: str | None = None
    date: str | None = None
    factor: float = 1.0


def combine(sigs: list[Sig], prior_weight: float = 1.0) -> tuple[float, float]:
    tw = sum(s.weight for s in sigs)
    score = (prior_weight * 0.5 + sum(s.weight * s.value for s in sigs)) / (prior_weight + tw)
    return score, tw / (tw + 2)


def confidence_label(c: float) -> str:
    return "high" if c >= 0.75 else "medium" if c >= 0.5 else "low"


ORDER = ["low", "mixed", "reliable", "highly_reliable"]


def basis(sigs: list[Sig], band: str, override: str | None = None) -> str | None:
    """'owner' (your override), 'expert' (a rating set), 'estimated' (measured evidence only), or None (unrated)."""
    if override:
        return "owner"
    if band == "unrated":
        return None
    return "expert" if any(s.signal in EXPERT for s in sigs) or band == "blocked" else "estimated"


def assign_band(score: float | None, confidence: float | None, sigs: list[Sig], th: dict[str, float], *, override: str | None = None) -> str:
    """override beats everything. Wikipedia's 'deprecated' (value 0 with the rsp signal) blocks.
    One rating set alone never calls a source Low: a verdict that harsh needs two independent sets to agree (see playbook 5.6).
    Without an expert signal: an estimated band when the estimate model is sure enough (th['estimate_hi'] / th['estimate_lo'])."""
    if override:
        return override
    if any(s.signal == "wikipedia_rsp" and s.value <= 0.0 for s in sigs):
        return "blocked"
    experts = [s for s in sigs if s.signal in EXPERT]
    if score is None or confidence is None:
        return "unrated"
    if not experts:
        p = next((s.value for s in sigs if s.signal == "estimate_model"), None)
        hi, lo = th.get("estimate_hi"), th.get("estimate_lo")
        if p is None:
            return "unrated"
        if hi is not None and p >= hi:
            return "reliable"                       # an estimate never says Highly reliable
        if lo is not None and p <= lo:
            return "mixed"                          # nor Low: too few sources are rated Low to learn it
        return "unrated"                            # the model is not sure enough either way
    if score >= th["highly"] and confidence >= 0.5:
        return "highly_reliable"
    if score >= th["reliable"]:
        return "reliable"
    if score >= th["mixed"] or len(experts) < 2:
        return "mixed"
    return "low"


def hysteresis(band: str, score: float | None, prev: str | None, th: dict[str, float], margin: float) -> str:
    """A source whose score only just crossed a threshold keeps its previous band, so noise in a weak signal cannot flip a band back and forth."""
    if score is None or prev not in ORDER or band not in ORDER or prev == band or margin <= 0:
        return band
    lo, hi = sorted((ORDER.index(prev), ORDER.index(band)))
    cuts = [th["mixed"], th["reliable"], th["highly"]]
    return prev if all(abs(score - c) < margin for c in cuts[lo:hi]) else band


def explain(sigs: list[Sig], band: str) -> str:
    """A short note for the source card when the band needs a caveat."""
    experts = [s for s in sigs if s.signal in EXPERT]
    notes = []
    if len(experts) == 1 and band in ("mixed", "reliable", "highly_reliable") and experts[0].value < 0.5:
        notes.append("Only one rating set covers this source, so it is shown as Mixed rather than Low until a second one agrees.")
    if len(experts) >= 2 and max(s.value for s in experts) - min(s.value for s in experts) >= 0.5:
        notes.append("The expert rating sets disagree about this source: see the evidence below.")
    if len(experts) == 1:
        notes.append("Based on one expert rating set.")
    if band == "unrated":
        notes.append("No expert rating set covers this source, and there is not yet enough measured evidence to estimate one. It is shown unrated, not unreliable.")
    elif not experts and band in ORDER:
        notes.append("No expert rating set covers this source: this band is an estimate from measured evidence (who cites it, the hyperlink-graph estimate, sister outlets, pickup by rated outlets). It never counts as a reliable outlet for a story's Confirmed tag.")
    return " ".join(notes)


def is_visible(band: str, floor: str, show_unrated: bool) -> bool:
    """floor 'all' (the default) shows every source, whatever its band: the owner decides from the marks, nothing is hidden for them."""
    if floor == "all":
        return True
    if band == "blocked":
        return False
    if band == "unrated":
        return show_unrated
    return BAND_RANK[band] >= BAND_RANK[floor]


def rank_weight(band: str, score: float | None, unrated_weight: float, gamma: float) -> float:
    if band in ("unrated", "blocked") or score is None:
        base = unrated_weight
    else:
        base = max(0.05, score)
    return round(base ** gamma, 4)


def rsp_value(label: str) -> float:
    return {"generally reliable": 0.85, "no consensus": 0.5, "generally unreliable": 0.15, "deprecated": 0.0, "blacklisted": 0.0}.get(label.lower(), 0.5)


# ---- weights learned from agreement (truth discovery) -------------------------------------------
def _cov(pairs: list[tuple[float, float]]) -> float:
    n = len(pairs)
    mx, my = sum(p[0] for p in pairs) / n, sum(p[1] for p in pairs) / n
    return sum((a - mx) * (b - my) for a, b in pairs) / n


def calibrate(rows: list[dict[str, tuple[float, float]]], priors: dict[str, float], *, n0: int = 200, cap: float = 3.0,
              min_pairs: int = 30) -> dict[str, dict]:
    """How much each kind of evidence should count, from how well it agrees with the others across all sources.

    One-factor model, as in truth discovery (Li et al. 2014, CRH) and in Lin et al. 2023's own principal component: every signal is
    the source's true quality plus its own noise. The loading of signal i is estimated from triads, l_i^2 = cov(i,j)cov(i,k)/cov(j,k)
    (median over all pairs j, k with 30 or more sources in common); its reliability is l_i^2 / var(i), and its weight the
    signal-to-noise ratio rel / (1 - rel), capped. The learned weight is then shrunk toward the prior weight in proportion to how
    many sources it was measured on: (n * learned + n0 * prior) / (n + n0). A signal that does not vary (ownership on record is
    always 0.75) carries no information and learns 0.

    rows: per source, signal -> (value 0..1, sample factor). Only values with factor >= 0.5 are used to learn."""
    import statistics
    sigs = sorted({k for r in rows for k in r})
    vals = {k: [r[k][0] for r in rows if k in r and r[k][1] >= 0.5] for k in sigs}
    var = {k: statistics.pvariance(v) if len(v) >= 2 else 0.0 for k, v in vals.items()}
    cov: dict[tuple[str, str], float | None] = {}
    for a in sigs:
        for b in sigs:
            if a < b:
                pairs = [(r[a][0], r[b][0]) for r in rows if a in r and b in r and r[a][1] >= 0.5 and r[b][1] >= 0.5]
                cov[(a, b)] = cov[(b, a)] = _cov(pairs) if len(pairs) >= min_pairs else None
    out = {}
    for i in sigs:
        n = len(vals[i])
        ests = []
        for j in sigs:
            for k in sigs:
                if j >= k or i in (j, k):
                    continue
                a, b, c = cov.get((i, j)), cov.get((i, k)), cov.get((j, k))
                if a is None or b is None or c is None or c <= 1e-6:
                    continue
                v = a * b / c
                if v > 0:
                    ests.append(v)
        prior = float(priors.get(i, 0.5))
        if var[i] < 1e-6:
            rel, learned = 0.0, 0.0
        elif not ests:
            rel, learned, n = None, prior, 0
        else:
            rel = min(0.95, statistics.median(ests) / var[i])
            learned = min(cap, rel / (1 - rel))
        w = (n * learned + n0 * prior) / (n + n0)
        out[i] = {"weight": round(w, 3), "learned": round(learned, 3), "reliability": None if rel is None else round(rel, 3), "n": n, "prior": prior,
                  "a": 0.0, "b": 1.0}
    # Measured evidence is put on the experts' scale: the expected expert score given the value (least squares on the sources experts
    # rated). A hyperlink-graph value near 1, usual for any mainstream site, then means "about as good as the typical rated site with
    # that value", not "the best there is".
    for i in sigs:
        if i in EXPERT or i not in out:
            continue
        pairs = []
        for r in rows:
            if i in r and r[i][1] >= 0.5:
                ex = [r[e][0] for e in EXPERT if e in r and r[e][1] >= 0.5]
                if ex:
                    pairs.append((r[i][0], sum(ex) / len(ex)))
        if len(pairs) >= min_pairs:
            mx = sum(p[0] for p in pairs) / len(pairs)
            my = sum(p[1] for p in pairs) / len(pairs)
            vx = sum((p[0] - mx) ** 2 for p in pairs) / len(pairs)
            b = max(0.0, _cov(pairs) / vx) if vx > 1e-9 else 0.0
            out[i]["a"], out[i]["b"] = round(my - b * mx, 4), round(b, 4)
    return out


def on_expert_scale(sigs: list[Sig], cal: dict) -> list[Sig]:
    """Copies of the signals with measured evidence mapped onto the experts' scale (calibrate); expert signals unchanged."""
    out = []
    for s in sigs:
        c = cal.get(s.signal)
        if c and s.signal not in EXPERT and (c.get("a"), c.get("b")) != (0.0, 1.0):
            out.append(Sig(s.signal, min(1.0, max(0.0, c["a"] + c["b"] * s.value)), s.weight, s.detail, s.url, s.date, s.factor))
        else:
            out.append(s)
    return out
