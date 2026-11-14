"""Checks an AI reply against the material it was given: every citation must name an id that was supplied, every cited statement
that contains a number must find that number in the cited text, and items with no valid citation are dropped (and counted).
The app shows the counts, so a reading that needed repair is visibly a repaired reading."""
from __future__ import annotations

import re
from typing import Any

_NUM = re.compile(r"\d[\d,.]*\d|\d")
CITED_LISTS_ARTICLE = ("key_points", "context", "people_and_groups", "numbers", "claims")
CITED_LISTS_STORY = ("summary", "established", "disputed", "single_source", "official_statements", "social_signals", "framing", "context", "what_to_watch")


_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_YEAR = re.compile(r"^(19|20)\d\d$")


def numbers_in(text: str) -> set[str]:
    """Digit numbers in a text (years left out), plus the digits for number words (so 'nine' in a source supports '9' in a summary)."""
    out = {n.replace(",", "").strip(".") for n in _NUM.findall(text)}
    out = {n for n in out if n and not _YEAR.match(n)}
    out |= {_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in _WORDS}
    return out


class Report:
    def __init__(self):
        self.dropped = 0
        self.bad_ids = 0
        self.unverified = 0

    def as_dict(self) -> dict:
        return {"dropped_uncited": self.dropped, "invalid_ids_removed": self.bad_ids, "numbers_not_found": self.unverified}


def _clean_cites(cites: Any, allowed: set[str], rep: Report) -> list[str]:
    if isinstance(cites, str):
        cites = [cites]
    if not isinstance(cites, list):
        return []
    good = [c for c in cites if isinstance(c, str) and c in allowed]
    rep.bad_ids += len(cites) - len(good)
    return list(dict.fromkeys(good))


def _item_text(it: dict) -> str:
    return " ".join(str(v) for k, v in it.items() if k not in ("cite", "positions") and isinstance(v, (str, int, float)))


def _check_numbers(it: dict, cited_text: str, rep: Report) -> None:
    nums = {n for n in numbers_in(_item_text(it)) if n.isdigit() or "." in n}
    if nums and not nums <= numbers_in(cited_text):
        it["unverified"] = True
        rep.unverified += 1


def clean_list(items: Any, allowed: set[str], texts: dict[str, str], rep: Report) -> list[dict]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        if "positions" in it:                                        # disputed point: cite inside each position
            pos = []
            for p in it.get("positions") or []:
                if isinstance(p, dict):
                    p["cite"] = _clean_cites(p.get("cite"), allowed, rep)
                    if p["cite"]:
                        _check_numbers(p, " ".join(texts.get(c, "") for c in p["cite"]), rep)
                        pos.append(p)
                    else:
                        rep.dropped += 1
            if pos:
                out.append({**it, "positions": pos})
            continue
        it["cite"] = _clean_cites(it.get("cite"), allowed, rep)
        if not it["cite"]:
            rep.dropped += 1
            continue
        _check_numbers(it, " ".join(texts.get(c, "") for c in it["cite"]), rep)
        out.append(it)
    return out


def validate_article(obj: dict, paragraphs: list[str]) -> tuple[dict, dict]:
    allowed = {f"p{i}" for i in range(1, len(paragraphs) + 1)}
    texts = {f"p{i}": p for i, p in enumerate(paragraphs, 1)}
    rep = Report()
    for k in CITED_LISTS_ARTICLE:
        obj[k] = clean_list(obj.get(k), allowed, texts, rep)
    obj["summary_cite"] = _clean_cites(obj.get("summary_cite"), allowed, rep)
    if not isinstance(obj.get("summary"), str) or not obj["summary"].strip():
        raise ValueError("no summary")
    if not obj["summary_cite"]:
        rep.dropped += 1
    for k in ("open_questions",):
        obj[k] = [x for x in obj.get(k) or [] if isinstance(x, str)]
    for k in ("kind", "neutral_headline", "framing_notes", "coverage_note"):
        obj[k] = str(obj.get(k) or "")
    return obj, rep.as_dict()


def validate_story(obj: dict, texts: dict[str, str]) -> tuple[dict, dict]:
    allowed = set(texts)
    rep = Report()
    for k in CITED_LISTS_STORY:
        obj[k] = clean_list(obj.get(k), allowed, texts, rep)
    if not obj["summary"]:
        raise ValueError("no cited summary")
    obj["unknowns"] = [x for x in obj.get("unknowns") or [] if isinstance(x, str)]
    obj["best_reads"] = [c for c in (obj.get("best_reads") or []) if c in allowed][:4]
    obj["confidence"] = obj.get("confidence") if obj.get("confidence") in ("high", "medium", "low") else "low"
    obj["neutral_headline"] = str(obj.get("neutral_headline") or "")
    # when the event itself happens (not when it was reported): kept only if it cites a supplied source
    tm = obj.get("timing") if isinstance(obj.get("timing"), dict) else {}
    cites = [c for c in (tm.get("cite") or []) if c in allowed]
    status = tm.get("status") if tm.get("status") in ("happened", "ongoing", "upcoming") else "unclear"
    obj["timing"] = {"status": status, "when": str(tm.get("when") or "")[:120], "cite": cites} if cites and status != "unclear" else {"status": "unclear", "when": "", "cite": []}
    return obj, rep.as_dict()


def validate_scope(obj: dict, texts: dict[str, str], story_ids: set[str]) -> tuple[dict, dict]:
    allowed = set(texts)
    rep = Report()
    obj["big_picture"] = clean_list(obj.get("big_picture"), allowed, texts, rep)
    obj["threads"] = clean_list(obj.get("threads"), allowed, texts, rep)
    obj["watch_next"] = clean_list(obj.get("watch_next"), allowed, texts, rep)
    stories = []
    for s in obj.get("stories") or []:
        if not isinstance(s, dict) or s.get("story_id") not in story_ids:
            continue
        for k in ("what_happened", "why_it_matters", "where_sources_differ"):
            s[k] = clean_list(s.get(k), allowed, texts, rep)
        if s["what_happened"]:
            stories.append(s)
    obj["stories"] = stories
    if not stories and not obj["big_picture"]:
        raise ValueError("nothing cited")
    obj["not_known_yet"] = [x for x in obj.get("not_known_yet") or [] if isinstance(x, str)]
    obj["title"] = str(obj.get("title") or "")
    return obj, rep.as_dict()
