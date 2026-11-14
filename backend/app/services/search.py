"""Search and Explore.

search: finds stories by exact words (PostgreSQL full text, stemmed) and by meaning (vectors), and fuses the two rankings.
explore: statistics for any query and filter over everything collected: volume over time, who covers it, where, how reliable,
which topics and places, the words that stand out. The same idea as Media Cloud's search tool, over the owner's own collection."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections import Counter

from app.services.stories import verification

UTC = dt.timezone.utc
STOP = set("""a an the of to in on for and or at by with from as is are was were be been it its this that these those after over amid says say said new will into out up down vs
not no but if than then so also more most about against between during under who what when where why how can could would should may might has have had do does did their his her
they them he she we you i our your us one two three year years day days week today yesterday video photos live latest news update updates report reports reportedly""".split())
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}")


def top_terms(titles: list[str], exclude: set[str], n: int = 40) -> list[dict]:
    uni, bi = Counter(), Counter()
    for t in titles:
        ws = [w.lower().strip("'’-") for w in WORD.findall(t)]
        ws = [w for w in ws if w not in STOP and w not in exclude]
        uni.update(set(ws))
        bi.update(set(zip(ws, ws[1:])))
    rows = [{"term": w, "n": c} for w, c in uni.most_common(n * 2)]
    rows += [{"term": f"{a} {b}", "n": c} for (a, b), c in bi.most_common(n) if c >= 3]
    rows.sort(key=lambda r: (-r["n"], r["term"]))
    seen, out = set(), []
    for r in rows:
        if r["term"] in seen:
            continue
        seen.add(r["term"])
        out.append(r)
    return out[:n]


def parse_when(v: str | None, default: dt.datetime) -> dt.datetime:
    if not v:
        return default
    try:
        d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Bad date {v}")
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class SearchService:
    def __init__(self, repo, settings, embedder, stories_svc, taxonomy, gazetteer):
        self.repo, self.settings, self.embedder, self.stories, self.tax, self.gaz = repo, settings, embedder, stories_svc, taxonomy, gazetteer

    async def search(self, q: str, *, place: str = "", topic: str = "", days: int = 30, limit: int = 30) -> dict:
        q = q.strip()
        if len(q) < 2:
            raise ValueError("Type at least two characters")
        place, topic = self.stories.check_scope(place, topic)
        place, topic = self.stories.place_codes(place), self.stories.topic_codes(topic)          # several places or topics; a group takes in its members
        since = dt.datetime.now(UTC) - dt.timedelta(days=max(1, min(days, 365)))
        lex = await asyncio.to_thread(self.repo.search_lexical, q, place=place, topic=topic, since=since, limit=60)
        sem, mode = [], "words"
        try:
            vecs, used = await self.embedder.embed([q])
            if vecs:
                sem = await asyncio.to_thread(self.repo.search_semantic, vecs[0], used, place=place, topic=topic, since=since, limit=60)
                sem = [r for r in sem if r["sim"] >= (0.30 if used == "model" else 0.12)]
                mode = "words + meaning" if used == "model" else "words + word-pair similarity"
        except RuntimeError:
            pass
        score: dict[int, float] = {}
        hit: dict[int, str] = {}
        for rank, r in enumerate(lex):
            score[r["id"]] = score.get(r["id"], 0) + 1 / (60 + rank)
            hit[r["id"]] = r["hit"]
        for rank, r in enumerate(sem):
            score[r["id"]] = score.get(r["id"], 0) + 1 / (60 + rank)
        ids = [i for i, _ in sorted(score.items(), key=lambda x: -x[1])][:limit]
        rows = {r["id"]: r for r in await asyncio.to_thread(self.repo.stories_by_ids, ids)}
        ordered = [rows[i] for i in ids if i in rows]
        srcs = await asyncio.to_thread(self.repo.story_sources, ids)
        tt, pt = await asyncio.to_thread(self.repo.story_tags, ids)
        now = dt.datetime.now(UTC)
        out = []
        for r in ordered:
            row = self.stories._row(r, srcs.get(r["id"], []), tt.get(r["id"], {}), pt.get(r["id"], {}), now, self.settings.get("feed.min_outlets"))
            row["hit"] = hit.get(r["id"])
            row["matched"] = ("words" if r["id"] in {x["id"] for x in lex} else "") + (" meaning" if r["id"] in {x["id"] for x in sem} else "")
            out.append(row)
        return {"query": q, "mode": mode, "stories": out, "count": len(out)}

    # ---- explore ---------------------------------------------------------------------------------
    def _filters(self, p: dict) -> dict:
        now = dt.datetime.now(UTC)
        until = parse_when(p.get("until"), now + dt.timedelta(minutes=1))
        days = int(p.get("days") or 7)
        since = parse_when(p.get("since"), until - dt.timedelta(days=max(1, min(days, 400))))
        if since >= until:
            raise ValueError("The start must be before the end")
        retention = int(self.settings.get("storage.retention_days"))
        f = {"q": (p.get("q") or "").strip(), "since": since, "until": until, "topic": p.get("topic") or "", "place": p.get("place") or "",
             "types": [t for t in (p.get("types") or []) if t], "bands": [b for b in (p.get("bands") or []) if b],
             "countries": [c.upper() for c in (p.get("countries") or []) if c], "sources": [s for s in (p.get("sources") or []) if s]}
        if f["place"] and f["place"] != "world" and f["place"] not in self.gaz.places:
            raise ValueError(f"Unknown place {f['place']}")
        if f["topic"] and f["topic"] not in self.tax.topics:
            raise ValueError(f"Unknown topic {f['topic']}")
        f["retention_note"] = f"Only the last {retention} days are kept, so earlier dates are empty." if (now - since).days > retention else ""
        return f

    @staticmethod
    def _bucket(f: dict) -> str:
        span = f["until"] - f["since"]
        return "hour" if span <= dt.timedelta(days=3) else "day" if span <= dt.timedelta(days=120) else "week"

    async def explore(self, p: dict) -> dict:
        f = self._filters(p)
        bucket = self._bucket(f)
        limit = max(1, min(int(p.get("limit") or 25), 100))
        d = await asyncio.to_thread(self.repo.explore, f, bucket, limit, max(0, int(p.get("offset") or 0)))
        titles = d.pop("titles")
        exclude = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", f["q"])}
        d["terms"] = top_terms(titles, exclude)
        d["topics"] = [{"code": r["code"], "name": self.tax.topics[r["code"]].name, "n": r["n"]} for r in d["topics"] if r["code"] in self.tax.topics]
        d["places"] = [{"code": r["code"], "name": self.gaz.places[r["code"]].name, "n": r["n"]} for r in d["places"] if r["code"] in self.gaz.places]
        d["filters"] = {**{k: v for k, v in f.items() if k not in ("since", "until")}, "since": f["since"].isoformat(), "until": f["until"].isoformat(), "bucket": bucket}
        n = d["totals"]["articles"]
        d["timeline"] = self._fill(d["timeline"], f, bucket)
        d["share_reliable"] = round(sum(r["n"] for r in d["bands"] if r["band"] in ("highly_reliable", "reliable")) / n, 3) if n else None
        d["articles_more"] = len(d["articles"]) == limit
        return d

    @staticmethod
    def _fill(rows: list[dict], f: dict, bucket: str) -> list[dict]:
        """Add the empty buckets so a quiet hour shows as zero, not as a gap."""
        step = {"hour": dt.timedelta(hours=1), "day": dt.timedelta(days=1), "week": dt.timedelta(days=7)}[bucket]
        by = {(r["t"].replace(tzinfo=UTC) if r["t"].tzinfo is None else r["t"]): r for r in rows}
        t = f["since"].astimezone(UTC)
        t = t.replace(minute=0, second=0, microsecond=0) if bucket == "hour" else t.replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket == "week":
            t -= dt.timedelta(days=t.weekday())
        out = []
        while t < f["until"] and len(out) < 2000:
            r = by.get(t)
            out.append({"t": t.isoformat(), "n": r["n"] if r else 0, "reliable": r["reliable"] if r else 0, "weak": r["weak"] if r else 0, "unrated": r["unrated"] if r else 0})
            t += step
        return out

    async def compare(self, queries: list[str], p: dict) -> dict:
        """Several queries over the same filters and period: one timeline each, to see which story moved when."""
        queries = [q.strip() for q in queries if q.strip()][:4]
        if len(queries) < 2:
            raise ValueError("Give two to four queries to compare")
        base = self._filters(p)
        bucket = self._bucket(base)
        series = []
        for q in queries:
            rows = await asyncio.to_thread(self.repo.explore_timeline_only, {**base, "q": q}, bucket)
            filled = self._fill([{**r, "reliable": 0, "weak": 0, "unrated": 0} for r in rows], base, bucket)
            series.append({"q": q, "total": sum(r["n"] for r in filled), "points": [{"t": r["t"], "n": r["n"]} for r in filled]})
        return {"bucket": bucket, "series": series}
