"""Read side: ranked story lists, story detail, and the counts shown in the place and topic menus."""
from __future__ import annotations

import datetime as dt
import time

from app.services import ranking
from app.services.dedupe import group_by_storyline, without_repeats
from app.services.spam import looks_like_ad
from app.services.subevents import find_subevents

UTC = dt.timezone.utc
LEVEL_RANK = {"city": 5, "state": 4, "country": 3, "subregion": 2, "group": 1, "region": 1}


class ScopeError(ValueError):
    pass


STRONG = ("reliable", "highly_reliable")


def verification(sources: list[dict]) -> dict:
    """How well a story stands, from the independent outlets that carry it and their trust bands.
    Outlets that print the same text (a wire story on twenty local stations) count once: ``grp`` is a fingerprint of the text.
    confirmed: two or more independent reliable outlets. reported: two or more independent outlets, fewer than two reliable.
    single: one reliable outlet. caution: one outlet that is not rated reliable, or only mixed/low/blocked outlets."""
    by_key = {x["key"]: x for x in sources}
    n = len(by_key)
    groups = {x.get("grp") or x["key"] for x in by_key.values()}
    # an estimated band (no expert rating behind it) never makes a story Confirmed: that estimate partly comes from such pickups
    strong_groups = {x.get("grp") or x["key"] for x in by_key.values() if x.get("band") in STRONG and x.get("basis") != "estimated"}
    strong, indep = len(strong_groups), len(groups)
    copies = f" ({indep} independent)" if indep < n else ""
    weak_only = n > 0 and all(x.get("band") in ("mixed", "low", "blocked") and x.get("basis") != "estimated" for x in by_key.values())
    if strong >= 2:
        return {"level": "confirmed", "label": f"Confirmed by {strong} independent reliable outlets" + (f", {n} outlets carry it" if indep < n else ""), "outlets": n, "reliable": strong, "independent": indep}
    if weak_only:
        return {"level": "caution", "label": "Only from sources rated Mixed or lower", "outlets": n, "reliable": strong, "independent": indep}
    if indep >= 2:
        return {"level": "reported", "label": f"Reported by {n} outlets{copies}, {strong} independent rated reliable", "outlets": n, "reliable": strong, "independent": indep}
    if strong == 1:
        return {"level": "single", "label": f"One reliable source so far" + (f" ({n} outlets carry the same text)" if n > 1 else ""), "outlets": n, "reliable": 1, "independent": indep}
    return {"level": "caution", "label": "One source, not rated reliable. Check before sharing" + (f" ({n} outlets carry the same text)" if n > 1 else ""), "outlets": n, "reliable": 0, "independent": indep}


def _bands(sources: list[dict]) -> dict[str, int]:
    """Outlets carrying a story by reliability band: {band: outlets}."""
    out: dict[str, int] = {}
    for x in {x["key"]: x for x in sources}.values():
        b = x.get("band") or "unrated"
        out[b] = out.get(b, 0) + 1
    return out


class StoryService:
    def __init__(self, repo, settings, taxonomy, gazetteer):
        self.repo, self.settings, self.tax, self.gaz = repo, settings, taxonomy, gazetteer
        self.cache_s = 20.0                         # seconds a Top or Trending list is reused (the page asks again after every burst of new stories)
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._sub: dict[tuple, list[dict]] = {}

    def _subevents(self, sid: int, arts: list[dict]) -> list[dict]:
        """The distinct parts of the story (services/subevents.py), computed from its stored headlines and reused while the story has the same number of them."""
        key = (sid, len(arts))
        hit = self._sub.get(key)
        if hit is not None:
            return hit
        reports = [{"title": a["title"], "published": min(a["published_at"], a["seen_at"]), "source": a["source_key"], "url": a["url"]} for a in arts]
        out = [dict(e) for e in find_subevents(reports, max_events=12)]
        if len(self._sub) > 300:
            self._sub.clear()
        self._sub[key] = out
        return out

    def clear_cache(self) -> None:
        self._cache.clear()

    def check_scope(self, place: str, topic: str) -> tuple[str, str]:
        """A scope is one or more places and zero or more topics, each list comma-separated ("IN,US", "sport.cricket,politics").
        A story is in it when it is about any of the places and any of the topics."""
        places = [p for p in dict.fromkeys(x.strip() for x in (place or "world").split(",")) if p] or ["world"]
        topics = [t for t in dict.fromkeys(x.strip() for x in (topic or "").split(",")) if t]
        for p in places:
            if p != "world" and p not in self.gaz.places:
                raise ScopeError(f"Unknown place {p}")
        for t in topics:
            if t not in self.tax.topics:
                raise ScopeError(f"Unknown topic {t}")
        if "world" in places:
            places = ["world"]                     # Global already includes every place
        return ",".join(places), ",".join(topics)

    def place_codes(self, place: str):
        """The place codes a scope matches: "world", or every chosen place with each group's member countries."""
        if place in ("", "world"):
            return "world"
        return list(dict.fromkeys(c for p in place.split(",") for c in self.gaz.scope_codes(p)))

    @staticmethod
    def topic_codes(topic: str):
        return [t for t in topic.split(",") if t] if topic else ""

    def scope_label(self, place: str, topic: str) -> str:
        pn = ", ".join(self._place(p)["name"] for p in place.split(",")) if place and place != "world" else ""
        tn = ", ".join(self.tax.topics[t].name for t in topic.split(",") if t in self.tax.topics) if topic else ""
        return " · ".join(x for x in (tn, pn) if x) or "Global"

    # ---- lists -----------------------------------------------------------------------------------
    TABS = ("top", "trending", "latest", "saved")
    VIEWS = ("fresh", "ongoing", "all")      # Top and Trending: stories first reported in the last 24 hours / begun earlier and still reported / both

    def langs_shown(self) -> list[str] | None:
        v = list(self.settings.get("feed.languages_shown") or [])
        return None if "all" in v or not v else v

    def list(self, place: str, topic: str, tab: str, limit: int | None = None, offset: int = 0, view: str = "all") -> dict:
        place, topic = self.check_scope(place, topic)
        s = self.settings
        if tab not in self.TABS:
            raise ScopeError("Unknown tab")
        if view not in self.VIEWS:
            raise ScopeError("Unknown view")
        limit = max(1, min(int(limit or s.get("feed.top_n")), 100))
        want = limit + offset
        p = ranking.Params.from_settings(s)
        ranked = tab in ("top", "trending")
        cached = ranked or tab == "latest"
        key = (place, topic, tab, view, limit, offset)
        hit = self._cache.get(key) if (cached and self.cache_s > 0) else None
        if hit and time.monotonic() - hit[0] < self.cache_s:
            return hit[1]
        rows = self.repo.list_stories(
            tab=tab, scopes=[(self.place_codes(place), self.topic_codes(topic))], min_outlets=s.get("feed.min_outlets"), allow_single=s.get("feed.single_source"),
            trending_h=p.trending_h, exclude_types=s.get("sources.exclude_types"), langs=self.langs_shown(), view=view, fresh_h=p.fresh_h, decay_h=p.decay_h,
            gravity=p.gravity, base_h=p.base_h, latest_hours=s.get("latest.hours"), latest_min=s.get("latest.min_outlets"), latest_gap_min=s.get("latest.spread_minutes"),
            latest_sure=s.get("latest.sure_outlets"), recent_h=p.active_h, limit=max(120, want * 3) if (ranked or tab == "latest") else want + 1, offset=0)
        if ranked:
            meta = {m["id"]: m for m in self.repo.story_vectors([r["id"] for r in rows])}
            rows = without_repeats(rows, meta, float(s.get("feed.story_gap_hours")), key=(ranking.surge if tab == "trending" else (lambda r: float(r["score"]))))
            rows = ranking.order(rows, tab, view, p, s.get("feed.min_outlets"))
            groups = group_by_storyline(rows, meta, eligible=lambda r: tab == "trending" or int(r.get("indep_p") or 0) >= int(s.get("feed.min_outlets")))         # developments of one storyline that are all in the list: one row that opens
        elif tab == "latest":
            rows = [r for r in rows if not looks_like_ad(r["title"], r.get("lead") or "")]            # an advertisement is not news, however many sites carry it
            meta = {m["id"]: m for m in self.repo.story_vectors([r["id"] for r in rows])}
            order = {r["id"]: i for i, r in enumerate(rows)}
            kept = without_repeats(rows, meta, float(s.get("feed.story_gap_hours")), key=lambda r: (int(r.get("indep") or 0), -order[r["id"]]))
            groups = [[r] for r in sorted(kept, key=lambda r: order[r["id"]])]                        # the same event told twice is one row; the newest first stays
        else:
            groups = [[r] for r in rows]
        groups = groups[offset:]
        more = len(groups) > limit
        groups = groups[:limit]
        ids = [r["id"] for g in groups for r in g]
        srcs = self.repo.story_sources(ids)
        tt, pt = self.repo.story_tags(ids)
        now = dt.datetime.now(UTC)
        min_out = s.get("feed.min_outlets")
        def make(r: dict) -> dict:
            return self._row(r, srcs.get(r["id"], []), tt.get(r["id"], {}), pt.get(r["id"], {}), now, min_out)
        stories = []
        for g in groups:
            row = make(g[0])
            if len(g) > 1:
                row["group"] = [make(c) for c in g[1:]]
            stories.append(row)
        out = {"scope": {"place": place, "topic": topic, "tab": tab, "view": view if ranked else None}, "more": more, "limit": limit, "stories": stories}
        if cached and self.cache_s > 0:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = (time.monotonic(), out)
        return out

    def _row(self, r: dict, sources: list[dict], ttags: dict, ptags: dict, now: dt.datetime, min_out: int) -> dict:
        # the outlet whose headline is shown (the headline is chosen from the reports: services/headlines.py), else the first to report
        lead = next((x for x in sources if x["key"] == r.get("lead_source")), None) or (sources[0] if sources else
                {"key": r["lead_source"], "name": r["lead_source"], "type": "news", "band": "unrated"})
        return {
            "id": r["id"], "title": r["title"], "lead": r["lead"], "first_seen": r.get("unit_first_seen") or r["first_seen"], "last_update": r["last_update"], "qualified_at": r.get("qualified_at"),
            "outlets": max(r["outlets"], int(r.get("outlets_p") or 0)), "indep": r.get("indep") or r["outlets"], "articles": r.get("unit_articles") or r["articles"],
            "parts": int(r.get("parts") or 1),
            "bucket": r.get("bucket"), "breaking": bool(r["breaking"]), "saved": bool(r["is_saved"]),
            "outlets_period": int(r["indep_p"]) if r.get("indep_p") is not None else None, "single_source": int(r.get("indep_p") or r.get("indep") or r["outlets"]) < min_out, "url": r["lead_url"],
            "title_en": r.get("title_en") if self.settings.get("translate.enabled") else None, "lang": r.get("lang"),
            "thread": {"id": r.get("thread_id") or r["id"], "developments": int(r.get("devs") or 1), "since": r.get("since") or r["first_seen"]},
            "developing": (now - r["first_seen"]) > dt.timedelta(hours=6) and (now - r["last_update"]) < dt.timedelta(hours=3),
            "lead_source": {"key": lead["key"], "name": lead["name"], "type": lead["type"], "band": lead.get("band", "unrated"), "basis": lead.get("basis")},
            "first_source": ({"key": sources[0]["key"], "name": sources[0]["name"]} if sources else None),
            "verification": verification(sources),
            "strip": [{"k": s["key"], "n": s["name"], "t": s["type"], "b": s["band"], "e": s.get("basis") == "estimated"} for s in sources],
            "bands": _bands(sources),
            "topic": self._best_topic(ttags, r["articles"]), "place": self._best_place(ptags, r["articles"]),
        }

    def _best_topic(self, tags: dict, articles: int) -> dict | None:
        ok = [c for c, n in tags.items() if n * 2 >= articles and c in self.tax.topics] or [c for c in tags if c in self.tax.topics]
        if not ok:
            return None
        c = max(ok, key=lambda c: (self.tax.topics[c].depth, tags[c]))
        return {"code": c, "name": self.tax.topics[c].name}

    def _best_place(self, tags: dict, articles: int) -> dict | None:
        ok = [c for c, n in tags.items() if n * 2 >= articles and c in self.gaz.places] or []
        if not ok:
            return None
        c = max(ok, key=lambda c: (LEVEL_RANK.get(self.gaz.places[c].level, 0), tags[c]))
        return {"code": c, "name": self.gaz.places[c].name}

    # ---- detail ----------------------------------------------------------------------------------
    def detail(self, sid: int) -> dict | None:
        r = self.repo.story_get(sid)
        if not r:
            return None
        arts = self.repo.story_articles(sid)
        excl = set(self.settings.get("sources.exclude_types"))
        tt, pt = self.repo.story_tags([sid])
        sources = self.repo.story_sources([sid]).get(sid, [])
        row = self._row({**r, "is_saved": r["is_saved"]}, sources, tt.get(sid, {}), pt.get(sid, {}), dt.datetime.now(UTC), self.settings.get("feed.min_outlets"))
        row["topics"] = [{"code": c, "name": self.tax.topics[c].name, "n": n} for c, n in sorted(tt.get(sid, {}).items(), key=lambda x: (-self.tax.topics[x[0]].depth, -x[1])) if c in self.tax.topics][:8]
        row["places"] = [{"code": c, "name": self.gaz.places[c].name, "n": n} for c, n in sorted(pt.get(sid, {}).items(), key=lambda x: (-LEVEL_RANK.get(self.gaz.places[x[0]].level, 0), -x[1])) if c in self.gaz.places][:8]
        tr = bool(self.settings.get("translate.enabled"))
        devs, minor = self.repo.thread_developments(r.get("thread_id") or sid, keep=sid)
        total, began = self.repo.thread_count(r.get("thread_id") or sid)
        row["thread"] = {"id": r.get("thread_id") or sid, "developments": max(total, len(devs) + minor), "since": began or min((d["first_seen"] for d in devs), default=r["first_seen"]), "minor": minor}
        row["timeline"] = [{"id": d["id"], "title": d["title"], "title_en": d["title_en"] if tr else None, "lang": d["lang"], "first_seen": d["first_seen"],
                            "last_update": d["last_update"], "indep": d["indep"], "breaking": d["breaking"],
                            "current": d["id"] == sid} for d in devs]

        def art(a: dict) -> dict:
            return {"id": a["id"], "url": a["url"], "title": a["title"], "lead": a["lead"], "published_at": a["published_at"],
                    "title_en": a.get("title_en") if tr else None, "lang": a.get("lang"),
                    "source": {"key": a["source_key"], "name": a["source_name"], "type": a["source_type"], "country": a["source_country"],
                               "domain": a["source_domain"], "provisional": a["provisional"], "band": a["source_band"], "score": a["source_score"],
                               "basis": a.get("source_basis")}}
        row["subevents"] = self._subevents(sid, arts)
        shown = [a for a in arts if a["source_visible"] and a["source_type"] not in excl]
        below = [a for a in arts if not a["source_visible"]]
        row["articles_list"] = [art(a) for a in shown]
        show_other = bool(self.settings.get("sources.show_other_coverage"))
        row["other_coverage"] = [art(a) for a in below] if show_other else []
        row["hidden_articles"] = sum(1 for a in arts if a["source_visible"] and a["source_type"] in excl) + (0 if show_other else len(below))
        row["reprints"] = [{"url": x["url"], "published_at": x["published_at"],
                            "source": {"key": x["source_key"], "name": x["source_name"], "type": x["source_type"], "country": x["source_country"], "band": x["source_band"],
                                       "basis": x["source_basis"]}}
                           for x in self.repo.story_reprints(sid) if x["source_visible"] and x["source_type"] not in excl]
        return row

    # ---- menus -----------------------------------------------------------------------------------
    def place_menu(self, place: str, topic: str) -> dict:
        """The children of one place (the menu can be browsed without changing the scope), counted for the scope's topics."""
        place, topic = self.check_scope(place, topic)
        place = place.split(",")[0]
        topic = self.topic_codes(topic)
        _, pf = self.repo.facets(place=self.place_codes(place), topic=topic, window_h=self.settings.get("feed.window_hours"), min_outlets=self.settings.get("feed.min_outlets"))
        win, mo = self.settings.get("feed.window_hours"), self.settings.get("feed.min_outlets")
        kids = [{"code": p.code, "name": p.name, "level": p.level, "count": pf.get(p.code, 0)} for p in self.gaz.children(place)]
        groups = []
        if place == "world":                     # groupings that cut across the continents: their counts take in every member country
            groups = [{"code": g.code, "name": g.name, "level": "group", "count": self.repo.scope_count(self.gaz.scope_codes(g.code), topic, win, mo)}
                      for g in sorted(self.gaz.groups(), key=lambda g: g.name)]
        return {"place": self._place(place), "breadcrumb": [self._place(p.code) for p in self.gaz.breadcrumb(place)], "children": kids, "groups": groups,
                "home": [self._place(self.settings.get(k)) for k in ("home.country", "home.state", "home.city") if self.settings.get(k) in self.gaz.places]}

    def _place(self, code: str) -> dict:
        p = self.gaz.places.get(code) if code != "world" else self.gaz.places["world"]
        return {"code": code, "name": p.name if p else code, "level": p.level if p else "world"}

    def index(self) -> dict:
        prefs = self.repo.topic_prefs()
        def tpath(c):
            return " › ".join([self.tax.topics[a].name for a in reversed(self.tax.ancestors(c))] + [self.tax.topics[c].name])
        if not hasattr(self, "_place_index"):
            # the jump box lists every region, country and state, and cities of 100,000 people or more (menus reach the rest)
            keep = [p for p in self.gaz.places.values() if p.level != "city" or p.featured or p.population >= 100_000]
            self._place_index = [{"code": p.code, "name": p.name, "level": p.level, "path": " › ".join(x.name for x in self.gaz.breadcrumb(p.code))} for p in keep]
        return {"places": self._place_index,
                "topics": [{"code": t.code, "name": t.name, "path": tpath(t.code), "enabled": t.depth == 1 or prefs.get(t.code, False)} for t in self.tax.topics.values()]}
