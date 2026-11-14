"""The headline and the line under it for a story and for a storyline, chosen from the reports themselves.

A story used to keep the headline of whichever report arrived first: often a one-line wire flash, a question, a live blog, or a
detail later overtaken by events. The latest report is no better on its own (it may be one outlet's side angle). Here every report's
headline is scored the way centroid-based multi-document summarisation scores sentences (MEAD, Radev et al. 2004): a headline is
representative when the words it uses are the words most reports of the story use. Each report's vote is weighted by how recent it
is (halving every ``half_life`` hours before the latest report), so the choice follows the story as it develops without letting one
late report decide, and by its outlet's reliability weight. Plain, informative headlines are preferred: questions, live blogs,
"watch"/"photos" items, headlines in capitals, very short or very long ones count less. Nothing is written by a model: the headline
is always one an outlet published, credited to it. The line under it is that report's own summary when it has a usable one,
otherwise the most representative summary of another report.

The same choice over all the reports of all developments of a storyline (half-life 24 hours) gives the storyline's title."""
from __future__ import annotations

import datetime as dt
import math
import re

from app.services.textutil import script_of, title_key

_LIVE = re.compile(r"\blive(?: updates?| blog| coverage|:)|\bas it happened\b|\bwatch(?: live)?\b|\bvideo\b|\bphotos?\b|\bin pictures\b|\bpodcast\b|"
                   r"\bwhat we know\b|\bexplained\b|\bopinion\b|\bcolumn\b|\bnewsletter\b|\bhighlights\b", re.I)
_SUFFIX = re.compile(r"\s+(?:[-|\u2013\u2014])\s+[^-|\u2013\u2014]{2,60}$")


def clean_title(t: str) -> str:
    """A headline without the publisher name its feed appends ("... - Reuters", "... | The Hindu")."""
    t = (t or "").strip()
    m = _SUFFIX.search(t)
    if m and len(t[:m.start()]) >= 25 and len(m.group(0).split()) <= 7:
        t = t[:m.start()].rstrip()
    return t


def quality(title: str) -> float:
    t = title.strip()
    if not t or t.startswith("@"):
        return 0.0                                     # social posts never title a story
    q = 1.0
    if t.endswith("?"):
        q *= 0.7
    if _LIVE.search(t):
        q *= 0.75
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        q *= 0.5
    n = len(t)
    if n < 35:
        q *= 0.75
    elif n > 140:
        q *= 0.8
    return q


def choose(reports: list[dict], idf: dict[str, float], default_idf: float, half_life: float = 12.0) -> tuple[int, list[float]]:
    """reports: {title, published (aware datetime), weight (outlet's reliability weight)}. Returns (index of the best, scores)."""
    if not reports:
        return -1, []
    latest = max(r["published"] for r in reports)
    votes, toks = [], []
    for r in reports:
        age = max(0.0, (latest - r["published"]).total_seconds() / 3600)
        votes.append(0.5 ** (age / half_life) * (0.5 + float(r.get("weight") or 0.46)))
        toks.append(set(title_key(clean_title(r["title"]))))
    total = sum(votes) or 1.0
    centroid: dict[str, float] = {}
    for v, ts in zip(votes, toks):
        for t in ts:
            centroid[t] = centroid.get(t, 0.0) + v
    scores = []
    for r, v, ts in zip(reports, votes, toks):
        if not ts:
            scores.append(0.0)
            continue
        mass = sum(idf.get(t, default_idf) * centroid[t] / total for t in ts)
        rep = mass / math.sqrt(len(ts))                  # longer headlines say more, but not in proportion
        scores.append(rep * quality(r["title"]) * (0.75 + 0.25 * v / max(votes)))
    best = max(range(len(reports)), key=lambda i: scores[i])
    return best, scores


def good_lead(title: str, lead: str) -> bool:
    lead = (lead or "").strip()
    if len(lead) < 60 or lead.startswith("Links to "):
        return False
    if lead.lower().startswith(title.lower()[:40]) and len(lead) < len(title) + 40:
        return False
    return script_of(lead) in ("", script_of(title))


def trim_lead(lead: str, limit: int = 320) -> str:
    lead = " ".join((lead or "").split())
    if len(lead) <= limit:
        return lead
    cut = lead[:limit]
    m = list(re.finditer(r"[.!?](?=\s)", cut))
    return cut[:m[-1].end()] if m and m[-1].end() > 80 else cut.rsplit(" ", 1)[0] + "\u2026"


def pick(reports: list[dict], idf: dict[str, float], default_idf: float, half_life: float = 12.0) -> dict | None:
    """The headline, its report and the line under it."""
    rs = [r for r in reports if r.get("title") and quality(r["title"]) > 0]
    if not rs:
        return None
    i, scores = choose(rs, idf, default_idf, half_life)
    best = rs[i]
    title = clean_title(best["title"])
    lead = best.get("lead") or ""
    if not good_lead(title, lead):
        # another report's summary, the most representative of those that are usable
        # another report's summary: the most representative usable one among reports that say much the same as the chosen headline
        mine = set(title_key(title))
        order = sorted(range(len(rs)), key=lambda k: -scores[k])
        lead = next((rs[k]["lead"] for k in order if good_lead(clean_title(rs[k]["title"]), rs[k].get("lead") or "")
                     and len(mine & set(title_key(clean_title(rs[k]["title"])))) * 2 >= min(len(mine), 6)), "")
    return {"title": title, "lead": trim_lead(lead), "url": best["url"], "source_key": best["source_key"], "index": i}


class HeadlineService:
    """After each refresh: the headline of every story that changed, and the title of every storyline that changed."""

    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    def _idf(self) -> tuple[dict[str, float], float]:
        df, n = self.repo.token_df(72)
        n = max(n, 1)
        return {t: math.log(n / (1 + c)) for t, c in df.items()}, math.log(n)

    def run(self, since_hours: float | None = None) -> dict:
        if not self.settings.get("feed.best_headline"):
            return {"off": True}
        last = self.repo.kv_get("headlines_at")
        since = dt.datetime.fromisoformat(last) if last and since_hours is None else dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_hours or 72)
        started = dt.datetime.now(dt.timezone.utc)
        idf, dflt = self._idf()
        changed = threads = 0
        by_story = self.repo.reports_of_changed_stories(since)
        updates = []
        for sid, reps in by_story.items():
            if len(reps) < 2 and not reps[0]["story_title"].startswith("@"):
                continue                                 # one outlet's report: its own headline, unless a social post titled the story
            p = pick(reps, idf, dflt, 12.0)
            if p and (p["title"] != reps[0]["story_title"] or p["url"] != reps[0]["story_url"]):
                updates.append((sid, p))
        changed = self.repo.story_headlines_set(updates)
        th_updates = []
        for th, reps in self.repo.reports_of_changed_threads(since).items():
            p = pick(reps, idf, dflt, 24.0)
            if p:
                th_updates.append((th, p))
        threads = self.repo.storyline_titles_set(th_updates)
        self.repo.kv_set("headlines_at", started.isoformat())
        return {"stories": len(by_story), "headlines_changed": changed, "storylines": threads}
