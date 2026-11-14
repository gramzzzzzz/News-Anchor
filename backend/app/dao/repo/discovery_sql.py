"""SQL for source discovery and the source lifecycle (mixed into Repository)."""
from __future__ import annotations

import json
from typing import Any

POLLABLE = ("active", "degraded", "dormant")


class DiscoverySQL:
    # ---- candidates ------------------------------------------------------------------------------
    def known_domains(self) -> dict[str, str]:
        """{domain: source key} for every source, so discovery never adds a site twice."""
        out = {d: "" for d in (self.kv_get("cleared_domains") or [])}          # sites the owner cleared are not added again
        out.update({r["domain"].lower().removeprefix("www."): r["key"] for r in self.q("SELECT domain, key FROM sources")})
        return out

    def source_keys(self) -> set[str]:
        return {r["key"] for r in self.q("SELECT key FROM sources")}

    def candidate_add(self, c: dict) -> bool:
        """Insert a discovered source as a candidate. Returns False when the key or Media Cloud id already exists.
        Scope is 'national' only when the publisher's country is known; otherwise 'international', so its placeless stories are not pinned to a guessed country."""
        c = {"scope": None, **c}
        return self.run("""INSERT INTO sources (key, name, domain, country, type, scope, provisional, curated, status, language, origin, mc_id, homepage,
                                                media_type, stories_per_week, discovered_at, status_note)
                           VALUES (%(key)s, %(name)s, %(domain)s, %(country)s, %(type)s, COALESCE(%(scope)s, CASE WHEN %(country)s = 'XX' THEN 'international' ELSE 'national' END), true, false, 'candidate', %(language)s, %(origin)s, %(mc_id)s,
                                   %(homepage)s, %(media_type)s, %(stories_per_week)s, now(), %(note)s)
                           ON CONFLICT DO NOTHING""", c) > 0

    def candidates_for_feed_search(self, limit: int, target: int = 30) -> list[dict]:
        """Waiting candidates, those from the countries with the fewest working sources first (a backlog from one well-covered country
        never holds up the rest of the world)."""
        return self.q("""WITH have AS (SELECT country, count(*) AS n FROM sources WHERE status IN ('active', 'degraded') AND type <> 'social' GROUP BY country)
                         SELECT s.key, s.name, s.domain, s.homepage, s.mc_id, s.country, s.language, s.origin, s.feed_search_tries
                         FROM sources s LEFT JOIN have h ON h.country = s.country
                         WHERE s.blocked = false AND (s.status = 'candidate' OR (s.status = 'no_feed' AND s.feed_search_at < now() - make_interval(days => LEAST(180, 30 * s.feed_search_tries))))
                         ORDER BY (s.country <> 'XX' AND COALESCE(h.n, 0) < %s) DESC, COALESCE(h.n, 0), (s.origin = 'wikidata') DESC, s.stories_per_week DESC NULLS LAST, s.discovered_at
                         LIMIT %s""", (target, limit))

    def country_source_counts(self) -> dict[str, int]:
        """Sources per publisher country that are working or waiting for a feed check (social accounts left out)."""
        return {r["country"]: r["n"] for r in self.q("""SELECT country, count(*) AS n FROM sources
                                                       WHERE status IN ('candidate', 'active', 'degraded') AND blocked = false AND type <> 'social' GROUP BY country""")}

    def rebalance(self, cap: int) -> dict[str, int]:
        """Keep at most ``cap`` working sources per country. Excess discovered sources are parked (not polled, kept with their feeds and history),
        the weakest first: no rating, then fewest articles. When a country drops under the cap (or the cap is raised), the best parked ones come back.
        Curated sources and sources of unknown country are never parked."""
        with self.pool.connection() as c:
            parked = c.execute("""
                WITH r AS (SELECT s.key, row_number() OVER (PARTITION BY s.country ORDER BY s.curated DESC, s.score DESC NULLS LAST,
                                  (SELECT COALESCE(sum(items_total), 0) FROM feeds f WHERE f.source_key = s.key) DESC, s.stories_per_week DESC NULLS LAST, s.discovered_at) AS rn
                           FROM sources s WHERE s.status IN ('candidate', 'active', 'degraded') AND s.blocked = false AND s.type <> 'social' AND s.country <> 'XX')
                UPDATE sources s SET status = 'parked', status_changed_at = now(),
                       status_note = 'Parked to keep countries in balance: its country already has the most sources allowed (Settings, Sources). Raise the limit to bring it back.'
                FROM r WHERE s.key = r.key AND r.rn > %s AND s.curated = false RETURNING s.key""", (cap,)).fetchall()
            back = c.execute("""
                WITH room AS (SELECT p.country, %s - COALESCE((SELECT count(*) FROM sources s WHERE s.country = p.country AND s.status IN ('candidate', 'active', 'degraded')
                                                            AND s.blocked = false AND s.type <> 'social'), 0) AS free
                              FROM (SELECT DISTINCT country FROM sources WHERE status = 'parked') p),
                     pick AS (SELECT s.key, row_number() OVER (PARTITION BY s.country ORDER BY s.score DESC NULLS LAST, s.stories_per_week DESC NULLS LAST) AS rn, room.free
                              FROM sources s JOIN room ON room.country = s.country WHERE s.status = 'parked')
                UPDATE sources s SET status = CASE WHEN EXISTS (SELECT 1 FROM feeds f WHERE f.source_key = s.key AND f.state NOT IN ('pending', 'retired', 'blocked')) THEN 'active' ELSE 'candidate' END,
                       status_changed_at = now(), status_note = 'Back from parking: its country has room again.'
                FROM pick WHERE s.key = pick.key AND pick.rn <= pick.free RETURNING s.key""", (cap,)).fetchall()
            if parked:
                c.cursor().executemany("INSERT INTO source_history (source_key, change, reason) VALUES (%s, 'parked', 'country balance')", [(r["key"],) for r in parked])
            if back:
                c.cursor().executemany("INSERT INTO source_history (source_key, change, reason) VALUES (%s, 'unparked', 'country balance')", [(r["key"],) for r in back])
        return {"parked": len(parked), "unparked": len(back)}

    def candidates_pending(self) -> int:
        """Sources waiting for a feed search: new candidates, and sources without a feed whose next try is due."""
        return self.one("""SELECT count(*) AS n FROM sources WHERE blocked = false AND (status = 'candidate'
                           OR (status = 'no_feed' AND feed_search_at < now() - make_interval(days => LEAST(180, 30 * feed_search_tries))))""")["n"]

    def pending_feeds(self, source_key: str) -> list[dict]:
        return self.q("SELECT id, url, kind, origin FROM feeds WHERE source_key = %s AND state = 'pending' ORDER BY id", (source_key,))

    def feed_pending_add(self, source_key: str, url: str, kind: str, origin: str) -> None:
        """A feed address someone else (Wikidata, Media Cloud) lists for the source. It is not polled until it has been validated."""
        self.run("INSERT INTO feeds (source_key, url, kind, origin, state) VALUES (%s, %s, %s, %s, 'pending') ON CONFLICT (url) DO NOTHING", (source_key, url, kind, origin))

    def feed_activate(self, feed_id: int, interval_s: int) -> None:
        self.run("UPDATE feeds SET state = 'active', fail_count = 0, poll_interval_s = %s, next_poll_at = now() + (random() * interval '10 minutes') WHERE id = %s", (interval_s, feed_id))

    def feed_delete(self, feed_id: int) -> None:
        self.run("DELETE FROM feeds WHERE id = %s", (feed_id,))

    def feed_discovered(self, source_key: str, url: str, kind: str, origin: str, interval_s: int) -> None:
        self.run("""INSERT INTO feeds (source_key, url, kind, origin, poll_interval_s, next_poll_at)
                    VALUES (%s, %s, %s, %s, %s, now() + (random() * interval '10 minutes')) ON CONFLICT (url) DO NOTHING""",
                 (source_key, url, kind, origin, interval_s))

    def source_feed_search_done(self, key: str, found: bool, note: str) -> None:
        if found:
            self.run("""UPDATE sources SET status = 'active', status_note = %s, status_changed_at = now(), feed_search_at = now(), feed_search_tries = feed_search_tries + 1
                        WHERE key = %s""", (note[:300], key))
            self.run("INSERT INTO source_history (source_key, change, reason) VALUES (%s, 'candidate to active', %s)", (key, note[:300]))
        else:
            self.run("""UPDATE sources SET status = 'no_feed', status_note = %s, status_changed_at = now(), feed_search_at = now(), feed_search_tries = feed_search_tries + 1
                        WHERE key = %s""", (note[:300], key))

    def no_feed_requeue(self) -> int:
        """Make every discovered source without a feed due for a fresh search now. They stay 'no_feed' (a source waiting as a candidate
        counts toward the limit on discovered sources; one without a feed does not), only the time of their last search moves back past
        the longest wait, so the ordinary feed-search batches take them in turn."""
        return self.run("""UPDATE sources SET feed_search_at = now() - interval '400 days', status_note = 'Re-check requested for all sources without a feed'
                           WHERE status = 'no_feed' AND curated = false AND blocked = false""")

    def no_feed_clear(self) -> int:
        """Delete every discovered source without a feed (and its feed rows, trust evidence and history). Its domain is remembered so that
        discovery does not add the site again. Sources that have articles or reprints stored are kept."""
        rows = self.q("""SELECT key, domain FROM sources s WHERE status = 'no_feed' AND curated = false
                         AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.source_key = s.key)
                         AND NOT EXISTS (SELECT 1 FROM reprints r WHERE r.source_key = s.key)""")
        if not rows:
            return 0
        keys = [r["key"] for r in rows]
        cleared = set(self.kv_get("cleared_domains") or []) | {r["domain"].lower().removeprefix("www.") for r in rows}
        self.kv_set("cleared_domains", sorted(cleared))
        for table in ("feeds", "trust_signals", "source_history", "storyline_titles"):
            self.run(f"DELETE FROM {table} WHERE source_key = ANY(%s)", (keys,))
        self.run("DELETE FROM source_links WHERE from_key = ANY(%s)", (keys,))
        return self.run("DELETE FROM sources WHERE key = ANY(%s) AND status = 'no_feed'", (keys,))

    def source_recheck(self, key: str) -> bool:
        """Owner asks for a fresh feed search (for a retired, dormant or feedless source)."""
        n = self.run("""UPDATE sources SET status = 'candidate', status_note = 'Re-check requested', status_changed_at = now(), feed_search_tries = 0 WHERE key = %s AND curated = false""", (key,))
        if n:
            self.run("UPDATE feeds SET state = 'active', fail_count = 0, retired_at = NULL, next_poll_at = now() WHERE source_key = %s AND state = 'retired'", (key,))
        return n > 0

    # ---- lifecycle -------------------------------------------------------------------------------
    def lifecycle_update(self, dormant_retire_days: int = 90) -> dict[str, int]:
        """Derive each source's status from its feeds.
        active: some feed polls fine. degraded: feeds are failing but not given up on. dormant: all feeds have failed for a long time.
        retired: dormant for ``dormant_retire_days`` (kept with its history; not polled; the owner can re-check it)."""
        with self.pool.connection() as c:
            rows = c.execute("""
                WITH f AS (SELECT s.key, s.status AS old,
                             count(*) FILTER (WHERE fe.state = 'active') AS a, count(*) FILTER (WHERE fe.state = 'degraded') AS d,
                             count(*) FILTER (WHERE fe.state = 'dormant') AS z, count(fe.id) FILTER (WHERE fe.state <> 'retired') AS n
                           FROM sources s JOIN feeds fe ON fe.source_key = s.key
                           WHERE s.status IN ('active', 'degraded', 'dormant') AND s.blocked = false GROUP BY s.key, s.status),
                     t AS (SELECT key, old, CASE WHEN n = 0 THEN 'retired' WHEN a > 0 THEN 'active' WHEN d > 0 THEN 'degraded' ELSE 'dormant' END AS new FROM f)
                UPDATE sources s SET status = t.new, status_changed_at = now(),
                       status_note = CASE t.new WHEN 'active' THEN '' WHEN 'degraded' THEN 'Its feeds are failing; still retried.' WHEN 'dormant' THEN 'No feed has worked for a long time; retried rarely.' ELSE 'Retired.' END
                FROM t WHERE s.key = t.key AND t.new <> t.old AND (s.curated = false OR t.new <> 'retired')
                RETURNING s.key, t.old AS old, t.new AS new""").fetchall()
            if rows:
                c.cursor().executemany("INSERT INTO source_history (source_key, change, reason) VALUES (%s, %s, 'feed health')", [(r["key"], f"{r['old']} to {r['new']}") for r in rows])
            ret = c.execute("""UPDATE sources SET status = 'retired', status_changed_at = now(), status_note = 'No feed worked for a long time; retired. Re-check to try again.'
                               WHERE status = 'dormant' AND curated = false AND status_changed_at < now() - make_interval(days => %s) RETURNING key""", (dormant_retire_days,)).fetchall()
            if ret:
                c.execute("UPDATE feeds SET state = 'retired', retired_at = now() WHERE source_key = ANY(%s)", ([r["key"] for r in ret],))
                c.cursor().executemany("INSERT INTO source_history (source_key, change, reason) VALUES (%s, 'dormant to retired', 'no feed worked for a long time')", [(r["key"],) for r in ret])
        out: dict[str, int] = {}
        for r in rows:
            out[r["new"]] = out.get(r["new"], 0) + 1
        out["retired"] = out.get("retired", 0) + len(ret)
        return out

    def source_status_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.q("SELECT status, count(*) AS n FROM sources GROUP BY status")}

    def feed_counts(self) -> dict[str, int]:
        return {r["state"]: r["n"] for r in self.q("SELECT state, count(*) AS n FROM feeds GROUP BY state")}

    def sources_page(self, *, q: str = "", status: str = "", band: str = "", country: str = "", typ: str = "", language: str = "", origin: str = "",
                     sort: str = "name", limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        where, p = ["TRUE"], {"lim": limit, "off": offset}
        if q:
            where.append("(s.name ILIKE %(q)s OR s.domain ILIKE %(q)s)"); p["q"] = f"%{q}%"
        for col, val, k in (("s.status", status, "st"), ("s.band", band, "bd"), ("s.country", country, "co"), ("s.type", typ, "ty"), ("s.language", language, "lg"), ("s.origin", origin, "og")):
            if val:
                where.append(f"{col} = %({k})s"); p[k] = val
        order = {"name": "s.name", "score": "s.score DESC NULLS LAST, s.name", "volume": "s.stories_per_week DESC NULLS LAST, s.name",
                 "recent": "(SELECT max(last_ok_at) FROM feeds f WHERE f.source_key = s.key) DESC NULLS LAST, s.name"}.get(sort, "s.name")
        w = " AND ".join(where)
        total = self.one(f"SELECT count(*) AS n FROM sources s WHERE {w}", p)["n"]
        rows = self.q(f"""SELECT s.key, s.name, s.domain, s.country, s.type, s.scope, s.provisional, s.curated, s.score, s.confidence, s.band, s.visible, s.owner,
                          s.override_band, s.trust_updated_at, s.trust_note, s.trust_basis, s.status, s.status_note, s.language, s.origin, s.stories_per_week,
                          (SELECT count(*) FROM feeds f WHERE f.source_key = s.key) AS feeds,
                          (SELECT count(*) FROM feeds f WHERE f.source_key = s.key AND f.state = 'active') AS feeds_active,
                          (SELECT max(last_ok_at) FROM feeds f WHERE f.source_key = s.key) AS last_ok_at
                          FROM sources s WHERE {w} ORDER BY {order} LIMIT %(lim)s OFFSET %(off)s""", p)
        return rows, total

    def source_facets(self) -> dict[str, Any]:
        return {
            "status": self.source_status_counts(),
            "band": {r["band"]: r["n"] for r in self.q("SELECT band, count(*) AS n FROM sources GROUP BY band")},
            "type": {r["type"]: r["n"] for r in self.q("SELECT type, count(*) AS n FROM sources GROUP BY type")},
            "language": {r["language"]: r["n"] for r in self.q("SELECT language, count(*) AS n FROM sources GROUP BY language")},
            "country": {r["country"]: r["n"] for r in self.q("SELECT country, count(*) AS n FROM sources GROUP BY country ORDER BY n DESC LIMIT 60")},
        }

    # ---- social accounts -------------------------------------------------------------------------
    def social_upsert(self, s: dict, interval_s: int) -> None:
        """One social account or timeline as a source of type 'social' with one feed (kind rss, bluesky, mastodon, reddit or x)."""
        with self.pool.connection() as c:
            c.execute("""INSERT INTO sources (key, name, domain, country, type, scope, provisional, curated, status, language, origin, status_note)
                         VALUES (%(key)s, %(name)s, %(domain)s, %(country)s, 'social', 'international', false, false, 'active', 'und', 'social', 'Social account from Settings')
                         ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, country = EXCLUDED.country, type = 'social',
                           status = CASE WHEN sources.status IN ('retired', 'dormant', 'parked') THEN 'active' ELSE sources.status END""", s)
            c.execute("""INSERT INTO feeds (source_key, url, kind, origin, place_mode, poll_interval_s, next_poll_at)
                         VALUES (%s, %s, %s, 'social', 'none', %s, now() + (random() * interval '2 minutes'))
                         ON CONFLICT (url) DO UPDATE SET source_key = EXCLUDED.source_key, kind = EXCLUDED.kind, origin = 'social',
                           state = CASE WHEN feeds.state = 'retired' THEN 'active' ELSE feeds.state END, retired_at = NULL,
                           fail_count = CASE WHEN feeds.state = 'retired' THEN 0 ELSE feeds.fail_count END""", (s["key"], s["url"], s["kind"], interval_s))

    def social_retire_except(self, urls: list[str]) -> int:
        """Social feeds no longer asked for stop being read (kept with their history; asking again brings them back)."""
        with self.pool.connection() as c:
            n = c.execute("""UPDATE feeds SET state = 'retired', retired_at = now() WHERE origin = 'social' AND state <> 'retired' AND NOT (url = ANY(%s))""", (urls,)).rowcount
            c.execute("""UPDATE sources s SET status = 'retired', status_note = 'No longer listed in Settings, Social.' WHERE s.type = 'social' AND s.status <> 'retired'
                         AND NOT EXISTS (SELECT 1 FROM feeds f WHERE f.source_key = s.key AND f.state <> 'retired')""")
        return n

    def social_feeds(self) -> list[dict]:
        return self.q("""SELECT f.url, f.kind, f.state, f.last_ok_at, f.last_error, f.items_total, s.name AS source_name FROM feeds f JOIN sources s ON s.key = f.source_key
                         WHERE f.origin = 'social' AND f.state <> 'retired' ORDER BY s.name""")

    # ---- web discovery ---------------------------------------------------------------------------
    def topic_story_counts(self, days: int = 7) -> dict[str, int]:
        return {r["code"]: r["n"] for r in self.q("""SELECT st.code, count(*) AS n FROM story_topics st JOIN stories s ON s.id = st.story_id AND NOT st.rejected
                                                   WHERE s.last_update > now() - make_interval(days => %s) GROUP BY st.code""", (days,))}

    def place_story_counts(self, days: int = 7) -> dict[str, int]:
        return {r["code"]: r["n"] for r in self.q("""SELECT sp.code, count(*) AS n FROM story_places sp JOIN stories s ON s.id = sp.story_id
                                                   WHERE s.last_update > now() - make_interval(days => %s) GROUP BY sp.code""", (days,))}

    def web_hit_add(self, h: dict) -> None:
        self.run("""INSERT INTO web_hits (domain, name, hits, queries, country, language, homepage, via, example)
                    VALUES (%(domain)s, %(name)s, %(hits)s, 1, %(country)s, %(language)s, %(homepage)s, %(via)s, %(example)s)
                    ON CONFLICT (domain) DO UPDATE SET hits = web_hits.hits + EXCLUDED.hits, queries = web_hits.queries + 1, last_seen = now(),
                      name = CASE WHEN web_hits.name = '' THEN EXCLUDED.name ELSE web_hits.name END,
                      country = CASE WHEN web_hits.country = '' THEN EXCLUDED.country ELSE web_hits.country END,
                      language = CASE WHEN web_hits.language = '' THEN EXCLUDED.language ELSE web_hits.language END""", h)

    def web_hits_merge(self, rows: list[dict]) -> None:
        """Add one observation run (an edition page sweep or a quarter hour of GDELT) to the running counts, one row per publisher.
        Row keys: domain, name, hits, queries (pages it appeared on), files (GDELT quarter hours), language, homepage, via, example,
        editions (Google News editions), nation_editions (countries whose national or local pages carried it), focus ({country: n})."""
        if not rows:
            return
        with self.pool.connection() as c:
            c.cursor().executemany("""
                INSERT INTO web_hits (domain, name, hits, queries, files, country, language, homepage, via, example, editions, nation_editions, focus)
                VALUES (%(domain)s, %(name)s, %(hits)s, %(queries)s, %(files)s, '', %(language)s, %(homepage)s, %(via)s, %(example)s, %(editions)s, %(nation_editions)s, %(focus)s::jsonb)
                ON CONFLICT (domain) DO UPDATE SET hits = web_hits.hits + EXCLUDED.hits, queries = web_hits.queries + EXCLUDED.queries, files = web_hits.files + EXCLUDED.files,
                  last_seen = now(),
                  name = CASE WHEN web_hits.name = '' THEN EXCLUDED.name ELSE web_hits.name END,
                  language = CASE WHEN web_hits.language = '' THEN EXCLUDED.language ELSE web_hits.language END,
                  via = CASE WHEN web_hits.via = '' OR position(EXCLUDED.via IN web_hits.via) > 0 THEN COALESCE(NULLIF(web_hits.via, ''), EXCLUDED.via) ELSE web_hits.via || ',' || EXCLUDED.via END,
                  editions = ARRAY(SELECT DISTINCT unnest(web_hits.editions || EXCLUDED.editions)),
                  nation_editions = ARRAY(SELECT DISTINCT unnest(web_hits.nation_editions || EXCLUDED.nation_editions)),
                  focus = COALESCE((SELECT jsonb_object_agg(k, n) FROM (SELECT k, sum(v::int) AS n FROM (SELECT * FROM jsonb_each_text(web_hits.focus)
                                    UNION ALL SELECT * FROM jsonb_each_text(EXCLUDED.focus)) x(k, v) GROUP BY k ORDER BY n DESC LIMIT 12) y), '{}'::jsonb)""",
                [{"name": "", "hits": 1, "queries": 0, "files": 0, "language": "", "homepage": None, "via": "", "example": "", "editions": [], "nation_editions": [], **r,
                  "focus": json.dumps(r.get("focus") or {})} for r in rows])

    def web_hits_ready(self, min_hits: int, min_queries: int, limit: int, min_files: int = 0) -> list[dict]:
        """Publishers seen often enough to be worth a feed check: on several search or edition pages, or (GDELT) in several separate quarter hours."""
        files = min_files or 1 << 30
        return self.q("""SELECT * FROM web_hits WHERE NOT promoted AND hits >= %s AND (queries >= %s OR files >= %s) ORDER BY hits DESC LIMIT %s""",
                      (min_hits, min_queries, files, limit))

    def web_hit_promoted(self, domain: str) -> None:
        self.run("UPDATE web_hits SET promoted = true WHERE domain = %s", (domain,))

    def web_stats(self) -> dict:
        return self.one("""SELECT count(*) AS seen, count(*) FILTER (WHERE promoted) AS promoted, count(*) FILTER (WHERE NOT promoted AND hits >= 2) AS waiting,
                                  count(*) FILTER (WHERE via LIKE '%%gdelt%%') AS gdelt, count(*) FILTER (WHERE via LIKE '%%google_news%%') AS google_news,
                                  count(DISTINCT language) AS languages FROM web_hits""")
