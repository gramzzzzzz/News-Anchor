"""SQL behind the Status page (mixed into Repository). Read-only summaries."""
from __future__ import annotations

from typing import Any

from app.dao.repo.discovery_sql import POLLABLE


class StatsSQL:
    def stats_news(self) -> dict[str, Any]:
        r = self.one("""SELECT (SELECT count(*) FROM articles) AS articles, (SELECT count(*) FROM articles WHERE seen_at > now() - interval '24 hours') AS articles_24h,
                        (SELECT count(*) FROM articles WHERE seen_at > now() - interval '7 days') AS articles_7d,
                        (SELECT count(*) FROM stories) AS stories, (SELECT count(*) FROM stories WHERE first_seen > now() - interval '24 hours') AS stories_24h,
                        (SELECT count(*) FROM stories WHERE outlets >= 2) AS stories_multi, (SELECT count(*) FROM stories WHERE breaking_until > now()) AS breaking_now,
                        (SELECT max(seen_at) FROM articles) AS last_article_seen, (SELECT max(published_at) FROM articles) AS last_article_published,
                        (SELECT max(first_seen) FROM stories) AS last_story_first_seen, (SELECT min(seen_day) FROM articles) AS oldest_day,
                        (SELECT count(DISTINCT source_key) FROM articles WHERE seen_at > now() - interval '24 hours') AS sources_reporting_24h""")
        r["per_day"] = self.q("SELECT seen_day AS day, count(*) AS n FROM articles WHERE seen_day >= current_date - 13 GROUP BY 1 ORDER BY 1")
        return r

    def stats_sources(self) -> dict[str, Any]:
        def grp(col: str) -> dict[str, int]:
            return {r["k"]: r["n"] for r in self.q(f"SELECT {col} AS k, count(*) AS n FROM sources GROUP BY 1 ORDER BY 2 DESC")}
        out = {"total": self.one("SELECT count(*) AS n FROM sources")["n"], "by_status": grp("status"), "by_band": grp("band"), "by_type": grp("type"),
               "by_origin": grp("origin"), "by_language": grp("language"), "curated": self.one("SELECT count(*) AS n FROM sources WHERE curated")["n"]}
        out["active_by_country"] = {r["country"]: r["n"] for r in self.q("SELECT country, count(*) AS n FROM sources WHERE status IN ('active', 'degraded') AND country <> 'XX' GROUP BY 1 ORDER BY 2 DESC")}
        out["discovered_per_day"] = self.q("SELECT discovered_at::date AS day, count(*) AS n FROM sources WHERE discovered_at > now() - interval '14 days' GROUP BY 1 ORDER BY 1")
        out["top_by_volume"] = self.q("""SELECT s.key, s.name, s.band, s.type, s.country, count(a.id) AS n FROM sources s JOIN articles a ON a.source_key = s.key
                                         WHERE a.seen_at > now() - interval '24 hours' GROUP BY 1, 2, 3, 4, 5 ORDER BY n DESC LIMIT 12""")
        return out

    def stats_feeds(self, languages: list[str] | None = None) -> dict[str, Any]:
        """Feed counts. "Collected" feeds are the ones the scheduler polls (pollable source, not blocked, enabled language); the rest are
        counted as ``not_collected`` so that they do not look overdue. ``late`` = collected feeds more than 15 minutes past their time."""
        r = self.one("""WITH c AS (SELECT f.* FROM feeds f JOIN sources s ON s.key = f.source_key
                                   WHERE s.blocked = false AND s.status = ANY(%(st)s) AND f.state NOT IN ('blocked', 'pending', 'retired')
                                   AND (%(langs)s::text[] IS NULL OR s.language = ANY(%(langs)s) OR s.type = 'social'))
                        SELECT (SELECT count(*) FROM feeds) AS total, count(*) AS collected,
                        count(*) FILTER (WHERE state = 'active' AND fail_count = 0) AS ok, count(*) FILTER (WHERE fail_count >= 3) AS failing,
                        count(*) FILTER (WHERE next_poll_at <= now()) AS due_now,
                        count(*) FILTER (WHERE next_poll_at < now() - interval '15 minutes') AS late,
                        count(*) FILTER (WHERE last_poll_at IS NULL) AS never_checked,
                        count(*) FILTER (WHERE last_poll_at > now() - interval '30 minutes') AS polled_last_30m,
                        min(next_poll_at) AS next_poll, max(last_poll_at) AS last_poll,
                        count(*) FILTER (WHERE last_poll_at > now() - interval '1 hour') AS polled_last_hour FROM c""",
                   {"st": list(POLLABLE), "langs": languages})
        r["not_collected"] = r["total"] - r["collected"]
        r["by_state"] = {x["state"]: x["n"] for x in self.q("SELECT state, count(*) AS n FROM feeds GROUP BY 1 ORDER BY 2 DESC")}
        r["by_kind"] = {x["kind"]: x["n"] for x in self.q("SELECT kind, count(*) AS n FROM feeds GROUP BY 1")}
        return r

    def jobs_recent(self, limit: int = 12) -> list[dict]:
        return self.q("SELECT id, kind, state, created_at, started_at, finished_at, left(result::text, 240) AS result FROM jobs ORDER BY id DESC LIMIT %s", (limit,))
