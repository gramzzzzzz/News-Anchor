"""Storylines: the Storylines tab, and the repair of stories and storylines that joined different events.

A storyline (thread) is a chain of developments: one event's reports per development, a new development when reports of the same
matter come a day or more later (playbook 20.11). The tab ranks storylines by how much independent coverage they drew in a chosen
period, counted fairly across countries, the way Google News "Full Coverage" and Event Registry's story view group an event's
reports over time and Story Forest (Liu et al. 2017) grows a story tree of related events.

Repair (once per REPAIR_VERSION, and on demand): reports that name different people, teams or places from the story's own headline
leave the story (anchors.different_events), and developments that are not related to the rest of their storyline leave it. Before
the anchor check existed, headline-word matching put Formula 1 and MotoGP Grand Prix reports in one story, and every later link to
that story joined the two series' storylines."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from app.services.anchors import anchors, different_events, rare_shared, rarity_bar, related
from app.services.textutil import title_key
from app.services.threads import SPLIT_MIN, repair_thread

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
RANGES = (6, 12, 24, 48, 72, 168, 336, 720)          # the periods the tab offers, in hours (up to 30 days)
REPAIR_VERSION = "2026-11-08a"


class StorylineService:
    def __init__(self, repo, settings, taxonomy, gazetteer, stories):
        self.repo, self.settings, self.tax, self.gaz, self.stories = repo, settings, taxonomy, gazetteer, stories
        self._cache: dict[tuple, tuple[float, dict]] = {}

    # ---- the tab ---------------------------------------------------------------------------------
    def list(self, place: str, topic: str, hours: int | None = None, limit: int = 20, offset: int = 0) -> dict:
        place, topic = self.stories.check_scope(place, topic)
        s = self.settings
        hours = int(hours or s.get("storylines.hours"))
        hours = min(RANGES, key=lambda r: abs(r - hours))
        limit = max(1, min(int(limit), 50))
        key = (place, topic, hours, limit, offset, s.get("storylines.min_developments"), s.get("feed.min_outlets"))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < 60:
            return hit[1]
        rows = self.repo.list_storylines(scopes=[(self.stories.place_codes(place), self.stories.topic_codes(topic))], hours=hours,
                                         min_devs=int(s.get("storylines.min_developments")), min_outlets=int(s.get("feed.min_outlets")),
                                         exclude_types=s.get("sources.exclude_types"), langs=self.stories.langs_shown(), limit=limit + 1, offset=offset)
        more = len(rows) > limit
        rows = rows[:limit]
        reps = [r["rep_id"] for r in rows]
        srcs = self.repo.story_sources(reps)
        tt, pt = self.repo.story_tags(reps)
        devs = self.repo.thread_developments_many([r["th"] for r in rows], hours)
        buckets = 24 if hours <= 72 else 14 if hours <= 336 else 30
        act = self.repo.thread_activity([r["th"] for r in rows], hours, buckets)
        now = dt.datetime.now(UTC)
        min_out = s.get("feed.min_outlets")
        tr = bool(s.get("translate.enabled"))
        out = []
        for r in rows:
            # a storyline of several developments is titled by its own headline (chosen from all its reports, services/headlines.py)
            own = {"title": r["line_title"], "lead": r["line_lead"], "lead_source": r["line_source"], "title_en": None} if r.get("line_title") else {}
            row = self.stories._row({**r, "id": r["rep_id"], "devs": r["devs_total"], "since": r["began"], **own}, srcs.get(r["rep_id"], []),
                                    tt.get(r["rep_id"], {}), pt.get(r["rep_id"], {}), now, min_out)
            ds, minor = devs.get(r["th"], ([], 0))
            row["storyline"] = {
                "id": r["th"], "score": round(float(r["score"] or 0), 3), "developments": int(r["devs_total"]), "developments_in_range": int(r["devs_in"]),
                "began": r["began"], "latest": r["latest"], "outlets_in_range": int(r["outlets_in"] or 0), "countries_in_range": int(r["countries_in"] or 0),
                "latest_title": r["latest_title"] if r["latest_id"] != r["rep_id"] else None, "latest_id": r["latest_id"],
                "activity": act.get(r["th"], [0] * buckets),
                "timeline": [{"id": d["id"], "title": d["title"], "title_en": d["title_en"] if tr else None, "lang": d["lang"], "first_seen": d["first_seen"],
                              "indep": d["indep"], "in_range": d["in_range"]} for d in ds],
                "minor": minor,
            }
            out.append(row)
        res = {"scope": {"place": place, "topic": topic, "hours": hours}, "ranges": list(RANGES), "more": more, "limit": limit, "storylines": out}
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), res)
        return res

    # ---- repair ----------------------------------------------------------------------------------
    async def repair_once(self) -> dict | None:
        if (await asyncio.to_thread(self.repo.kv_get, "storyline_repair")) == REPAIR_VERSION:
            return None
        res = await asyncio.to_thread(self.repair)
        await asyncio.to_thread(self.repo.kv_set, "storyline_repair", REPAIR_VERSION)
        log.warning("Storyline repair: %s", res)
        return res

    def repair(self, days: int = 7) -> dict:
        frequent, _n = self.repo.frequent_tokens(72, rarity_bar)
        out = {"stories_checked": 0, "articles_moved": 0, "stories_made": 0, "threads_checked": 0, "developments_moved": 0}
        names: dict[str, set[str]] = {}

        def nm(title: str) -> set[str]:
            if title not in names:
                if len(names) > 200_000:
                    names.clear()
                names[title] = anchors(title, self.gaz)
            return names[title]

        # 1. reports in a story that name other things than the story's own headline
        touched: set[int] = set()
        for st in self.repo.stories_for_repair(days):
            arts = self.repo.story_article_titles(st["id"])
            if len(arts) < 2:
                continue
            out["stories_checked"] += 1
            lead = next((a for a in arts if a["url"] == st["lead_url"]), arts[0])
            ln, lt = nm(lead["title"]), title_key(lead["title"])
            keep, away = [], []
            for a in arts:
                if a is lead or (a["fp"] and a["fp"] == lead["fp"]):
                    keep.append(a)
                elif different_events(nm(a["title"]), ln, title_key(a["title"]), lt, frequent):
                    away.append(a)
                else:
                    keep.append(a)
            if not away:
                continue
            # an article stays if it clearly belongs with most of the reports that stay (the story's headline may be the odd one)
            named = [k for k in keep if len(nm(k["title"])) >= 2]
            really = []
            for a in away:
                clash = sum(1 for k in named if different_events(nm(a["title"]), nm(k["title"]), title_key(a["title"]), title_key(k["title"]), frequent))
                (really if clash * 2 >= len(named) else keep).append(a)
            # a report naming a single thing that no other report of the story names, and sharing nothing distinctive with the story's
            # headline ("Hamilton 'lost for words' after claiming pole" inside the MotoGP sprint story)
            if len(arts) >= 5:
                count: dict[str, int] = {}
                for k in keep:
                    for x in nm(k["title"]):
                        count[x] = count.get(x, 0) + 1
                profile = {x for x, n in count.items() if n * 4 >= len(keep)}
                if len(profile) >= 2:
                    for a in list(keep):
                        an = nm(a["title"])
                        if a is lead or len(an) != 1 or (a["fp"] and a["fp"] == lead["fp"]):
                            continue
                        (x,) = an
                        if x not in profile and count.get(x, 0) <= 1 and not (an & ln) \
                                and not rare_shared(title_key(a["title"]), lt, an | ln, frequent):
                            keep.remove(a)
                            really.append(a)
            if not really:
                continue
            groups: list[list[dict]] = []
            for a in really:                                     # the reports that leave, grouped by what they name
                an, at = nm(a["title"]), title_key(a["title"])
                for g in groups:
                    gn, gt = nm(g[0]["title"]), title_key(g[0]["title"])
                    if not different_events(an, gn, at, gt, frequent) and related(an, gn, at, gt, frequent):
                        g.append(a)
                        break
                else:
                    groups.append([a])
            for g in groups:
                new_id = self.repo.story_split(st["id"], g, title_key(g[0]["title"]))
                if new_id:
                    touched.add(new_id)
                    out["stories_made"] += 1
                    out["articles_moved"] += len(g)
            touched.add(st["id"])
        if touched:
            self.repo.stories_rebuild(list(touched), reset_vectors=True)
        # 2. developments that are not part of their storyline
        for th, devs in self.repo.threads_for_repair(days).items():
            if len(devs) < 2:
                continue
            out["threads_checked"] += 1
            devs.sort(key=lambda d: (d["first_seen"], d["id"]))
            info = {d["id"]: (nm(d["title"]), title_key(d["title"])) for d in devs}
            left = list(devs)
            comps: list[list[dict]] = []
            while left:
                root = left.pop(0)
                comp, frontier = [root], [root]
                rn, rt = info[root["id"]]
                while frontier:
                    x = frontier.pop()
                    xn, xt = info[x["id"]]
                    for y in list(left):
                        yn, yt = info[y["id"]]
                        if related(xn, yn, xt, yt, frequent) and not different_events(xn, yn, xt, yt, frequent) \
                                and not different_events(rn, yn, rt, yt, frequent):
                            comp.append(y)
                            frontier.append(y)
                            left.remove(y)
                comps.append(comp)
            for comp in comps[1:]:
                new_th = comp[0]["id"]
                out["developments_moved"] += self.repo.thread_assign([d["id"] for d in comp], new_th)
        # 3. storylines whose developments have drifted apart: re-clustered by average linkage (services/threads.py)
        out.update(storylines_split=0, storylines_made=0)
        cohesion = float(self.settings.get("storylines.cohesion"))
        ids = self.repo.threads_to_check(SPLIT_MIN)
        for t, (n, coh) in self.repo.thread_coherence(ids).items():
            if coh < cohesion:
                r = repair_thread(self.repo, self.gaz, t, frequent, cohesion=cohesion)
                if r["groups"] > 1:
                    out["storylines_split"] += 1
                    out["storylines_made"] += r["groups"]
        if out["storylines_split"]:
            self.repo.kv_set("headlines_at", (dt.datetime.now(UTC) - dt.timedelta(hours=72)).isoformat())      # titles of the new storylines are chosen next refresh
            self._cache.clear()
        if out["articles_moved"]:
            self.repo.recompute_coverage(days)
        return out
