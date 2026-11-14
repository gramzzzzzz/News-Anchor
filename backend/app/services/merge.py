"""After each refresh: give new stories a meaning vector and fold reports of the same event into one story.

The refresh first groups articles by shared headline words and by identical text (wire copy), cheaply. This pass catches the same
event worded differently. Calibrated on 210 hand-labelled pairs of real stories (playbook 8.6; scripts/cluster_eval.py):

    merge when  cos >= 0.80,  or  cos >= 0.65 and the two headlines share rare words (IDF mass >= 0.10 of the smaller side)
    never when  either is routine content (horoscopes, forecasts, police logs, obituaries, best bets, briefs, mugshots ...),
                both name a city or state and they have none in common (two shootings in two towns),
                both give casualty counts and none agree ("2 dead" vs "12 dead"), unless cos >= 0.82 and they name one place (a revised toll),
                the cluster centroids drift apart (average linkage >= 0.62), which stops chains of near-neighbours.

On that sample: precision 0.92, recall 0.86 (the rule used before this pass: precision 0.92, recall 0.10).
Vectors exist only for English text (or an English translation): the English model gives meaningless similarities between
headlines in other scripts, so those never merge by meaning, only by shared words or identical text."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import re
from collections import Counter

from app.services.anchors import anchors, different_events, rarity_bar, related
from app.services.textutil import title_key
from app.services.threads import SPLIT_MIN, ThreadGuard, repair_thread

log = logging.getLogger(__name__)
_NUM = re.compile(r"\d[\d,.]*")
ROUTINE = re.compile(r"horoscope|tarot|zodiac|\bforecast\b|weather (?:update|outlook)|first alert|police (?:report|log|blotter)s?|\bobituar|"
                     r"death notices|best bets|\bpicks\b|news ?brief|\bbriefs\b|sample ballots?|\bquiz\b|\bcrossword\b|\bwordle\b|^\d{5,}\s|"
                     r"\blottery\b|\bmugshots?\b|around the region|things to do|this week in|trick-or-treat|letters? to the editor|"
                     r"(?:playoff|football|game|sports|basketball|soccer|volleyball|hockey|baseball) scores|scoreboard|scores and highlights|highlights and scores|"
                     r"(?:sports|game|tv|broadcast) schedule|high school (?:football|sports|flag football|volleyball|basketball|soccer|hockey|baseball)|"
                     r"\bbulletin\b|week in (?:images|pictures|photos|review)|\bin (?:photos|pictures)\b|photos of the (?:day|week)|funeral services|"
                     r"moon phase|who lives here|printed letters|past 100 years|on this day|today in history|"
                     r"\$[A-Z]{1,5}\b.*\b(?:shares|stake|position|holdings)\b|\b(?:shares|stake|position|holdings) (?:of|in) .{2,60}\b(?:sold|bought|purchased|acquired|raised|lowered|trimmed|boosted|cut) by\b|"
                     r"\b(?:buys|sells|acquires|purchases) [\d,]+ (?:shares|stake) (?:of|in)\b|"
                     r"^(?!.*\b(?:hurricane|typhoon|cyclone|tornado|blizzard|heatwave|heat wave|wildfire|kill|dead|death|evacuat|emergency)).*"
                     r"\b(?:showers?|sunshine|cloudy|(?:morning|patchy|dense) fog|drizzle|rain chances?|chances? of (?:rain|snow|storms?)|weather (?:pattern|blog|forecast|changes)|"
                     r"accuweather|seasonable|breezy|chilly|dry weekend|wet (?:start|weather)|weekend weather|milder (?:temperatures|weather|air)|temperatures turning|mild (?:start|weekend|week)|(?:strong|severe) (?:thunder)?storms? possible|"
                     r"severe (?:potential|weather risk)|thunderstorms? (?:possible|likely)|rain (?:tonight|tomorrow|overnight|returns|likely|this (?:evening|weekend)))\b|"
                     r"^[^:|—–]{2,40}(?:[:|(]|\s[—–-])\s*(?:\w+,?\s)?(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}(?:st|nd|rd|th)?|\d{1,2}(?:st|nd|rd|th)? (?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*)(?:\s?[-–]\s?\d{1,2})?,? \d{4}\)?\s*(?:[-–|]\s*\w+)?$|"
                     r"^(?:morning|evening|daily|weekly|nightly|midday|afternoon) (?:briefing|bulletin|digest|news ?letter|roundup|update)\b|"
                     r"\beverything you need to know about the polls\b", re.I)
_CASUALTY = re.compile(r"(\d+)\s+(?:people\s+)?(?:dead|killed|die|died|injured|hurt|wounded|missing)", re.I)
T_HIGH, T_LOW, RARE_MIN, CENTROID_MIN = 0.80, 0.65, 0.10, 0.62
BIG_STORY = 40                   # outlets: a story this big joins another only when their headlines are alike (cos >= the high bar)
SINGLE_HOURS = 6                # a pass that is not a full re-cluster compares one-outlet stories of this many hours only
BIG_PAIR_COS = 0.90              # two established stories (both BIG_STORY outlets or more) are one event only at this similarity or above
UPDATED_TOLL_COS = 0.82          # a casualty clash is read as a revised toll (4 dead, then 7) when the headlines are this alike and name one place


def same_event_in_time(a: dict, b: dict, max_gap: float, max_span: float) -> bool:
    """Two stories can be one event only if their reports overlap in time or pause no longer than ``max_gap`` seconds between them,
    and together they last no longer than ``max_span``. (Their first reports alone said little: a stale or date-only time on one early
    report would cut a story that is still being reported off from its own newest reports.)"""
    first = min(a["first_seen"], b["first_seen"])
    last = max(a["last_update"], b["last_update"])
    gap = max(a["first_seen"], b["first_seen"]) - min(a["last_update"], b["last_update"])
    return gap.total_seconds() <= max_gap and (last - first).total_seconds() <= max_span


def numbers(title: str) -> set[str]:
    out = {n.strip(",.").replace(",", "") for n in _NUM.findall(title)}
    return {n for n in out if n and not (len(n) == 4 and n.isdigit() and 1990 <= int(n) <= 2040)}          # a year is not a count


def routine(title: str) -> bool:
    """Not merged by wording: routine content, or a headline too short to tell events apart."""
    return bool(ROUTINE.search(title or "")) or len(title_key(title or "")) <= 2


def routine_item(title: str) -> bool:
    """Routine content (scoreboards, schedules, bulletins, obituaries, dated galleries): listed, never ranked in Top or Trending."""
    return bool(ROUTINE.search(title or ""))


def casualty_clash(a: str, b: str) -> bool:
    na, nb = {int(x) for x in _CASUALTY.findall(a)}, {int(x) for x in _CASUALTY.findall(b)}
    return bool(na and nb and not (na & nb))


def rare_overlap(ta: set[str], tb: set[str], idf: dict[str, float]) -> float:
    sa, sb = sum(idf.get(w, 0.0) for w in ta), sum(idf.get(w, 0.0) for w in tb)
    return sum(idf.get(w, 0.0) for w in ta & tb) / max(1e-6, min(sa, sb))


def decide(cos: float, rare: float, title_a: str, title_b: str, places_a: set[str], places_b: set[str], ancestors, t_high: float = T_HIGH) -> bool:
    """The calibrated pair rule (module docstring). ``places_*`` are city- and state-level codes; ``ancestors(code)`` gives parents."""
    if routine(title_a) or routine(title_b):
        return False
    if not (cos >= t_high or (cos >= T_LOW and rare >= RARE_MIN)):
        return False
    shared = False
    if places_a and places_b:
        wa = places_a | {x for c in places_a for x in ancestors(c)}
        wb = places_b | {x for c in places_b for x in ancestors(c)}
        shared = bool((wa & wb) & (places_a | places_b))
        if not shared:
            return False
    if casualty_clash(title_a, title_b) and not (cos >= UPDATED_TOLL_COS and shared):
        return False                                 # "2 dead" and "12 dead" are two events unless the wording and the place say it is one with a revised toll
    return True


def can_merge(a_title: str, b_title: str, a_tokens: list[str], b_tokens: list[str], min_shared: int) -> bool:
    """Kept for callers outside the pass: the cheap guards only."""
    if len(set(a_tokens) & set(b_tokens)) < min_shared or routine(a_title) or routine(b_title):
        return False
    return not casualty_clash(a_title, b_title)


def _release_memory() -> None:
    import ctypes
    import gc
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


class _NoGate:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class StoryMerger:
    def __init__(self, repo, settings, embedder, bus, gazetteer=None):
        self.repo, self.settings, self.embedder, self.bus, self.gaz = repo, settings, embedder, bus, gazetteer
        self.gate = None                                 # the ingest service's store gate: the pass takes it only to write (set by the container)
        self._lock = asyncio.Lock()                      # one pass at a time: the pass after a refresh and a full re-cluster must not interleave

    def _fine(self, codes: set[str]) -> set[str]:
        if not self.gaz:
            return set()
        return {c for c in codes if c in self.gaz.places and self.gaz.places[c].level in ("city", "state")}

    @staticmethod
    def _joinable(i: int, j: int, rows, names, toks, frequent, roots) -> bool:
        """Two developments days apart join one storyline only with positive evidence (a shared name or rare word), and only if neither
        names other things than the first development of the other's storyline (so one loose link cannot chain two storylines)."""
        if not related(names(i), names(j), toks[i], toks[j], frequent):
            return False
        for x, y in ((i, j), (j, i)):
            r = roots.get(rows[y].get("thread_id"))
            if r is not None and r != y and different_events(names(x), names(r), toks[x], toks[r], frequent):
                return False
        return True

    async def run(self, max_new: int = 3000, full: bool = False, recent_hours: int | None = None) -> dict:
        """``recent_hours``: with ``full``, compare only the stories begun in that many hours and the big ones (20 outlets or more) against
        every multi-outlet story of the window and each other: the repair after a change of the grouping rules, small enough to run next to the polling."""
        async with self._lock:
            try:
                return await self._run(max_new, full, recent_hours)
            finally:
                await asyncio.to_thread(_release_memory)             # the pass held several hundred MB of small objects; give back what can be given back

    async def _run(self, max_new: int, full: bool, recent_hours: int | None = None) -> dict:
        backend = await self.embedder.backend()
        if backend == "off":
            return {"backend": "off"}
        todo = await asyncio.to_thread(self.repo.stories_to_embed, 4, max_new, backend)
        used = backend
        if todo:
            vecs, used = await self.embedder.embed([f"{t['text']}. {t['lead'][:160]}" for t in todo])
            if used == "unavailable":                         # the model failed this time: keep the existing vectors and try next refresh
                return {"backend": backend, "embedded": 0, "merged": 0, "note": "meaning model unavailable this pass"}
            await asyncio.to_thread(self.repo.story_embeddings_set, [(t["id"], v) for t, v in zip(todo, vecs)], used)
        await asyncio.to_thread(self.repo.kv_set, "embed_backend", used)
        new_ids = {t["id"] for t in todo}
        if not new_ids and not full:
            return {"backend": used, "embedded": 0, "merged": 0}
        apply = await asyncio.to_thread(self._cluster, used, new_ids, full, recent_hours)      # reads and compares: no writes, so polling goes on meanwhile
        async with (self.gate or _NoGate()):                                     # writes: one writer at a time, like the feeds' own storing
            merged = await asyncio.to_thread(apply)
            try:
                self._split = await asyncio.to_thread(self._recheck)
            except Exception:                                # noqa: BLE001 - keeping storylines tidy must not fail the pass
                log.exception("Storyline coherence check failed")
                self._split = 0
            try:
                await asyncio.to_thread(self.repo.link_chains, float(self.settings.get("feed.development_hours")), float(self.settings.get("feed.story_gap_hours")))
            except Exception:                                # noqa: BLE001 - marking continuations must not fail the pass
                log.exception("Linking the continuations of long stories failed")
            if merged and self.settings.get("breaking.enabled"):
                brk = (self.settings.get("breaking.outlets"), self.settings.get("breaking.minutes"), self.settings.get("breaking.mark_hours"))
                await asyncio.to_thread(lambda: [self.repo.mark_breaking(keep, *brk) for keep in self._last_keeps])
        if merged:
            self.bus.publish({"type": "stories", "merged": merged})
        return {"backend": used, "embedded": len(todo), "merged": merged, "threads_joined": self._links, "threads_split": getattr(self, "_split", 0)}

    def _recheck(self) -> int:
        """Storylines that grew or changed in the last few hours and have drifted apart (their developments average under the setting
        storylines.cohesion in similarity to the centre) are split again. Returns how many were split."""
        ids = self.repo.threads_to_check(SPLIT_MIN, active_hours=3)
        if not ids:
            return 0
        settled = self.__dict__.setdefault("_settled", {})               # storyline -> size at which re-clustering left it whole
        cohesion = float(self.settings.get("storylines.cohesion"))
        bad = [t for t, (n, coh) in self.repo.thread_coherence(ids).items() if coh < cohesion and settled.get(t) != n]
        if not bad:
            return 0
        frequent, _n = self.repo.frequent_tokens(72, rarity_bar)
        done = 0
        for t in bad:
            r = repair_thread(self.repo, self.gaz, t, frequent, cohesion=cohesion)
            if r["groups"] > 1:
                log.warning("Storyline %s had drifted apart (%d developments): split into %d", t, r["before"], r["groups"])
                done += 1
            else:
                settled[t] = r["before"]
        if len(settled) > 5000:
            settled.clear()
        return done

    def _cluster(self, backend: str, new_ids: set[int], full: bool, recent_hours: int | None = None):
        """Average-linkage merging over the last 48 hours, in memory. New stories are compared with every story in the window;
        with ``full`` every story is (used once after an upgrade, to fold the backlog). Reads and compares only; returns a function that
        writes the result and returns how many stories were merged (the caller runs it under the store gate)."""
        import numpy as np
        self._last_keeps = []
        self._links = 0
        rows, vecs = [], []
        # a story with one outlet that began long ago does not take new reports (they would have joined it when they came); leaving them out
        # of the window keeps the pass small: 87% of the stories of two days are such (memory: the pass was killed at the container limit)
        hours = recent_hours or (None if full else SINGLE_HOURS)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours) if hours else None
        for r in self.repo.window_rows(48, backend):                      # about 1.5 KB a story in memory (vector as float32), not 5 KB of text
            if cutoff and (r["outlets"] or 0) < 2 and r["first_seen"] < cutoff:
                continue                                                  # a repair run leaves out old one-outlet stories (memory)
            vecs.append(np.fromstring(r.pop("e")[1:-1], sep=",", dtype=np.float32))
            rows.append(r)
        if len(rows) < 2:
            return lambda: 0
        thr = float(self.settings.get("search.merge_threshold"))         # the high bar; default 0.80
        t_high = thr if thr > 0 else T_HIGH
        ids = [r["id"] for r in rows]
        pos = {sid: i for i, sid in enumerate(ids)}
        E = np.vstack(vecs)
        del vecs
        E /= np.maximum(np.linalg.norm(E, axis=1, keepdims=True), 1e-9)
        toks = [set(r["tokens"]) for r in rows]
        df = Counter(w for s in toks for w in s)
        n = len(rows)
        idf = {w: math.log(n / (1 + c)) for w, c in df.items()}
        places = [self._fine(set(r["places"] or [])) for r in rows]
        anc = (lambda c: self.gaz.ancestors(c)) if self.gaz else (lambda c: [])
        queries = [i for i, r in enumerate(rows) if r['first_seen'] >= cutoff or (r['outlets'] or 0) >= 20] if (full and cutoff) else list(range(n)) if full else [pos[i] for i in new_ids if i in pos]
        bar = rarity_bar(n)
        frequent = {w for w, c in df.items() if c > bar}
        cache: dict[int, set[str]] = {}

        def names(i: int) -> set[str]:
            if i not in cache:
                cache[i] = anchors(rows[i]["title"], self.gaz)
            return cache[i]
        roots: dict[int, int] = {}                                   # each thread's first development in the window
        for i, r in enumerate(rows):
            t = r.get("thread_id")
            if t not in roots or r["first_seen"] < rows[roots[t]]["first_seen"]:
                roots[t] = i
        edges, links = [], []
        guard = ThreadGuard(self.repo.thread_info, float(self.settings.get("storylines.cohesion")))               # storylines join only while their centroids stay close (services/threads.py)
        max_span = int(self.settings.get("feed.development_hours")) * 3600          # a story takes reports for this long after its first...
        max_gap = int(self.settings.get("feed.story_gap_hours")) * 3600            # ...unless its reports pause for this long
        for start in range(0, len(queries), 256):
            q = queries[start:start + 256]
            S = E[q] @ E.T
            for k, i in enumerate(q):
                S[k, i] = -1.0
                cand = np.where(S[k] >= T_LOW)[0]
                if len(cand) > 12:
                    cand = cand[np.argsort(-S[k, cand])[:12]]
                for j in cand:
                    j = int(j)
                    cos = float(S[k, j])
                    a, b = rows[i], rows[j]
                    if a.get("outlets", 2) <= 1 and b.get("outlets", 2) <= 1 and a.get("lead_source") == b.get("lead_source"):
                        continue                                     # two items of one source only: templated headlines, not one event
                    rare = rare_overlap(toks[i], toks[j], idf)
                    if cos < BIG_PAIR_COS and min(a.get("outlets") or 0, b.get("outlets") or 0) >= BIG_STORY:
                        continue                                     # two established stories are two events, whatever their centres say (broad subjects have alike centres); only near-identical ones are a duplicate
                    if cos < t_high and max(a.get("outlets") or 0, b.get("outlets") or 0) >= BIG_STORY:
                        continue                                     # a story with dozens of outlets does not take in another on the weak test (0.65 and a shared rare word): it would swallow every angle on its subject
                    if decide(cos, rare, a["title"], b["title"], places[i], places[j], anc, t_high):
                        if different_events(names(i), names(j), toks[i], toks[j], frequent):
                            continue                                 # alike in wording, but they name different people, teams, places
                        if same_event_in_time(a, b, max_gap, max_span):
                            edges.append((cos, min(i, j), max(i, j)))          # the same event: one development
                        elif a.get("thread_id") != b.get("thread_id") and self._joinable(i, j, rows, names, toks, frequent, roots):
                            links.append((cos, a["id"], b["id"]))              # same storyline, events days apart: one thread
        def apply() -> int:
            if links:
                # the older development's thread absorbs the newer one's, if the two storylines are still alike as wholes
                guard.preload([rows[pos[x]]["thread_id"] for _c, a, b in links for x in (a, b)])
                pairs = []
                for _c, x, y in sorted(set(links), reverse=True):
                    (older, newer) = (x, y) if rows[pos[x]]["first_seen"] <= rows[pos[y]]["first_seen"] else (y, x)
                    io, inw = pos[older], pos[newer]
                    to, tn = rows[io]["thread_id"], rows[inw]["thread_id"]
                    if guard.ok(to, tn, E[io], E[inw]):
                        guard.join(to, tn, E[io], E[inw])
                        pairs.append((older, newer))
                self._links = self.repo.link_threads(pairs) if pairs else 0
            if not edges:
                return 0
            # Greedy average linkage: strongest pairs first; two groups join only if their centroids are still close. A group with a big story (40 outlets
            # or more) takes in another group only if the other group's largest story is itself close to the big group's centre (0.80, or 0.90 if
            # that is a big story too): the pair test alone let a small story in between pull a distinct event (30 outlets, 0.71 to the big story)
            # into it, a chain of links that each passed.
            parent = list(range(n))
            sums = {i: E[i].copy() for i in range(n)}
            size = {i: 1 for i in range(n)}
            members_of = {i: [i] for i in range(n)}
            outlets_of = {i: int(rows[i].get("outlets") or 0) for i in range(n)}

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def centroid(r: int):
                return sums[r] / size[r]

            def cosine(a, b) -> float:
                return float(a @ b) / max(1e-9, float(np.linalg.norm(a) * np.linalg.norm(b)))

            def chained(ri: int, rj: int) -> bool:
                if max(outlets_of[ri], outlets_of[rj]) < BIG_STORY:
                    return False
                big, small = (ri, rj) if outlets_of[ri] >= outlets_of[rj] else (rj, ri)
                top = max(members_of[small], key=lambda m: int(rows[m].get("outlets") or 0))          # the biggest story of the smaller group
                need = BIG_PAIR_COS if outlets_of[small] >= BIG_STORY else t_high
                return cosine(E[top], centroid(big)) < need

            for cos, i, j in sorted(set(edges), reverse=True):
                ri, rj = find(i), find(j)
                if ri == rj:
                    continue
                if cosine(centroid(ri), centroid(rj)) < CENTROID_MIN or chained(ri, rj):
                    continue
                parent[rj] = ri
                sums[ri] = sums[ri] + sums[rj]
                size[ri] += size[rj]
                members_of[ri] = members_of[ri] + members_of.pop(rj)
                outlets_of[ri] += outlets_of.pop(rj)
            groups: dict[int, list[int]] = {}
            for i in range(n):
                groups.setdefault(find(i), []).append(i)
            merged = 0
            for members in groups.values():
                if len(members) < 2:
                    continue
                members.sort(key=lambda i: (rows[i]["first_seen"], rows[i]["id"]))
                keep = rows[members[0]]["id"]
                ki = members[0]
                for i in members[1:]:
                    # the dropped story's storyline comes along only if it is the same storyline as the kept one's
                    tk, td = rows[ki].get("thread_id"), rows[i].get("thread_id")
                    carry = tk == td
                    if not carry and self._joinable(ki, i, rows, names, toks, frequent, roots) and guard.ok(tk, td, E[ki], E[i]):
                        guard.join(tk, td, E[ki], E[i])
                        carry = True
                    if self.repo.merge_stories(keep, rows[i]["id"], carry_thread=carry) is not None:
                        merged += 1
                self._last_keeps.append(keep)
            return merged
        return apply
