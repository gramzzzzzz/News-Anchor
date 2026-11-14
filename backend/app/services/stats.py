"""One read-only summary of the whole application for the Status page: what has been collected, from whom, when each job last ran,
and how well the world is covered. Nothing here changes anything."""
from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc


class StatsService:
    def __init__(self, repo, settings, taxonomy, gazetteer, ai_router, embedder, discovery, started: dt.datetime | None = None):
        self.repo, self.settings, self.tax, self.gaz = repo, settings, taxonomy, gazetteer
        self.ai, self.embedder, self.discovery = ai_router, embedder, discovery
        self.started = started or dt.datetime.now(UTC)
        self._pending: tuple[float, dict] | None = None

    def cleanup(self) -> dict:
        """The clean-up of stories only one outlet reported (services/storage.py): what it has deleted so far, and what is waiting (counted at most once a minute)."""
        import time
        wait = float(max(int(self.settings.get("storage.purge_wait_hours")), int(self.settings.get("refresh.max_item_age_hours")) + 12))
        if self._pending is None or time.monotonic() - self._pending[0] > 60:
            w = self.repo.one("""SELECT count(*) AS n FROM stories WHERE outlets <= 1 AND last_update >= now() - (%s * interval '1 hour') AND NOT ('social' = ANY(types))""", (wait,))["n"]
            self._pending = (time.monotonic(), {"waiting": w})
        st = self.repo.kv_get("purge_stats") or {}
        return {"enabled": bool(self.settings.get("storage.purge_uncorroborated")), "wait_hours": wait, "waiting": self._pending[1]["waiting"],
                "stories": int(st.get("stories", 0)), "articles": int(st.get("articles", 0)), "ads": int(st.get("ads", 0)), "last_at": st.get("last_at"),
                "last": st.get("last"), "since": st.get("since"), "per_day": st.get("per_day") or {}}

    def snapshot(self) -> dict:
        r = self.repo
        news, srcs, feeds = r.stats_news(), r.stats_sources(), r.stats_feeds(None if self.settings.get("sources.all_languages") else list(self.settings.get("sources.languages")))
        tcount, pcount = r.topic_story_counts(7), r.place_story_counts(7)
        prefs = r.topic_prefs()
        cats = [{"code": c, "name": self.tax.topics[c].name, "stories": tcount.get(c, 0),
                 "subtopics_active": sum(1 for d in self.tax.descendants(c) if tcount.get(d, 0) > 0), "subtopics": len(self.tax.descendants(c))} for c in self.tax.roots]
        subs = sorted(((c, n) for c, n in tcount.items() if c in self.tax.topics and self.tax.topics[c].depth >= 2), key=lambda x: -x[1])[:20]
        enabled = [c for c, t in self.tax.topics.items() if t.depth == 1 or prefs.get(c)]
        quiet = [self.tax.topics[c].name for c in enabled if tcount.get(c, 0) == 0 and self.tax.topics[c].depth >= 2]
        places = self.gaz.places
        regions = sorted(({"code": c, "name": p.name, "stories": pcount.get(c, 0)} for c, p in places.items() if p.level == "region"), key=lambda x: -x["stories"])
        countries = [(c, p) for c, p in places.items() if p.level == "country"]
        with_src = srcs["active_by_country"]
        with_story = {c for c, _ in countries if pcount.get(c, 0) > 0}
        gaps = sorted(p.name for c, p in countries if c not in with_src)
        top_countries = sorted(({"code": c, "name": p.name, "stories": pcount.get(c, 0), "sources": with_src.get(c, 0)} for c, p in countries), key=lambda x: -x["stories"])[:30]
        dstatus = self.discovery.status()
        trust_run = r.kv_get("trust_last_run") or {}
        usage = r.ai_usage_summary()
        emb = r.embedded_count(r.kv_get("embed_backend"))
        return {
            "now": dt.datetime.now(UTC), "app_started": self.started,
            "news": news,
            "refresh": {"interval_minutes": self.settings.get("refresh.news_minutes"), "last_poll": feeds["last_poll"], "next_poll": feeds["next_poll"], "due_now": feeds["due_now"],
                        "polled_last_hour": feeds["polled_last_hour"], "paused": bool(r.kv_get("ingest_paused", False)), "last_job": r.job_last("refresh-news")},
            "sources": srcs, "feeds": feeds,
            "discovery": {"last_directory": dstatus["last_directory"], "last_web": dstatus["last_web"], "last_batch": dstatus["last_batch"], "lifecycle_last": r.kv_get("lifecycle_last"),
                          "pending_feed_search": dstatus["pending_feed_search"], "web": dstatus["web"], "running": {"directory": dstatus["directory_busy"], "batch": dstatus["batch_busy"], "web": dstatus["web_running"]},
                          "mediacloud_token": dstatus["mediacloud_token"], "ceiling": self.settings.get("discovery.max_total"), "languages": self.settings.get("sources.languages"),
                          "all_languages": bool(self.settings.get("sources.all_languages")),
                          "last_stream": dstatus["last_stream"], "last_editions": dstatus["last_editions"], "last_wikidata": dstatus["last_wikidata"],
                          "last_rebalance": dstatus["last_rebalance"], "directory_resume": dstatus["directory_resume"], "coverage": dstatus["coverage"], "busy": dstatus["busy"]},
            "translate": self.translator.status() if getattr(self, "translator", None) else None,
            "prefetch": self.prefetch.status() if getattr(self, "prefetch", None) else None,
            "social": self.social.status() if getattr(self, "social", None) else None,
            "trust": {"last_run": {k: trust_run.get(k) for k in ("at", "sources", "seconds", "bands", "notes")} if trust_run else None, "waiting_to_be_rated": r.one("SELECT count(*) AS n FROM sources WHERE trust_updated_at IS NULL AND status IN ('active', 'degraded', 'dormant')")["n"]},
            "categories": {"top": cats, "busiest_subtopics": [{"code": c, "name": self.tax.topics[c].name, "stories": n} for c, n in subs],
                           "enabled_but_quiet": quiet[:40], "enabled_but_quiet_count": len(quiet), "topics_total": len(self.tax.topics)},
            "geography": {"regions": regions, "top_countries": top_countries, "countries_total": len(countries), "countries_with_active_source": sum(1 for c, _ in countries if c in with_src),
                          "countries_with_stories_7d": len(with_story), "countries_without_source": gaps[:120], "countries_without_source_count": len(gaps)},
            "ai": {"calls_today": usage["calls_today"], "ready": self.ai.any_usable() and bool(self.settings.get("ai.enabled")), "providers_ready": sum(1 for p in self.ai.public_state() if p["enabled"])},
            "search": {"embedded": emb["embedded"], "stories": emb["stories"], "backend": r.kv_get("embed_backend"), "model_loaded": self.embedder.loaded},
            "storage": {"database_bytes": r.db_bytes(), "retention_days": self.settings.get("storage.retention_days"), "oldest_day": news["oldest_day"]},
            "cleanup": self.cleanup(),
            "jobs": r.jobs_recent(12),
        }
