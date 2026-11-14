"""All SQL lives here (as in Portfolio Tracker's sqlite_repo). Services call methods; they never see SQL.

Synchronous psycopg with a small pool: API handlers are plain ``def`` (FastAPI runs them in a thread pool)
and the async ingest loop reaches the database through ``asyncio.to_thread``."""
from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.dao.repo.discovery_sql import POLLABLE, DiscoverySQL
from app.dao.repo.search_sql import SearchSQL
from app.dao.repo.stats_sql import StatsSQL

log = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
UTC = dt.timezone.utc


# Outlets, independent outlets and reliability-weighted coverage of one story. Each outlet counts once (its first article);
# outlets whose article has the same text fingerprint (wire copy) form one group, worth the best weight in the group.
# Fair coverage: each wire-copy group is credited to the country of its best-weighted outlet; within a country the credit grows as
# log2(1 + weight sum), so the hundredth outlet of one country adds little and a new country adds a lot. Social posts earn none.
STORY_COUNTS = """WITH d AS (SELECT DISTINCT ON (x.source_key) x.source_key, COALESCE(x.fp, x.source_key) AS fp FROM (
                               SELECT a.source_key, a.fp, a.published_at FROM articles a WHERE a.story_id = %(sid)s AND a.seen_day >= %(floor)s
                               UNION ALL SELECT r.source_key, r.fp, r.published_at FROM reprints r WHERE r.story_id = %(sid)s) x
                           ORDER BY x.source_key, x.published_at),
                       v AS (SELECT d.fp, so.weight, so.country, so.type FROM d JOIN sources so ON so.key = d.source_key WHERE so.visible),
                       g AS (SELECT DISTINCT ON (fp) fp, weight AS w, country, type FROM v ORDER BY fp, weight DESC),
                       cc AS (SELECT country, sum(w) AS w FROM g WHERE type <> 'social' GROUP BY country)"""
# Every outlet that carried a story: its own articles, and the reprints (word-for-word copies, kept only as who/when/link).
COVERAGE_ROWS = """(SELECT story_id, source_key, published_at, seen_day FROM articles
                    UNION ALL SELECT story_id, source_key, published_at, seen_day FROM reprints)"""
STORY_COUNTS_SET = """outlets = (SELECT count(*) FROM v), indep = GREATEST((SELECT count(*) FROM g), LEAST(1, (SELECT count(*) FROM v))),
                      coverage = (SELECT COALESCE(sum(w), 0) FROM g),
                      fair = (SELECT COALESCE(sum(ln(1 + w) / ln(2)), 0) FROM cc)"""


def part_name(day: dt.date) -> str:
    return "articles_" + day.strftime("%Y%m%d")


class Repository(DiscoverySQL, SearchSQL, StatsSQL):
    def __init__(self, url: str, max_size: int = 6):
        self.pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False,
                                   kwargs={"row_factory": dict_row}, check=ConnectionPool.check_connection)

    # ---- lifecycle -------------------------------------------------------------------------------
    def open(self) -> None:
        self.pool.open(wait=True, timeout=60)
        self.migrate()

    def close(self) -> None:
        self.pool.close()

    def migrate(self) -> None:
        files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
        with self.pool.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(727001)")
            have = c.execute("SELECT to_regclass('schema_version') IS NOT NULL AS ok").fetchone()["ok"]
            done = {r["version"] for r in c.execute("SELECT version FROM schema_version")} if have else set()
            for f in files:
                v = int(f.name[:3])
                if v in done:
                    continue
                log.info("Applying migration %s", f.name)
                c.execute(f.read_text(encoding="utf-8"))
                c.execute("INSERT INTO schema_version (version) VALUES (%s)", (v,))

    def q(self, sql: str, params: Any = None) -> list[dict]:
        with self.pool.connection() as c:
            return c.execute(sql, params).fetchall()

    def one(self, sql: str, params: Any = None) -> dict | None:
        with self.pool.connection() as c:
            return c.execute(sql, params).fetchone()

    def run(self, sql: str, params: Any = None) -> int:
        with self.pool.connection() as c:
            return c.execute(sql, params).rowcount

    # ---- settings, kv, audit ---------------------------------------------------------------------
    def settings_all(self) -> dict[str, Any]:
        return {r["key"]: r["value"] for r in self.q("SELECT key, value FROM settings")}

    def setting_set(self, key: str, value: Any) -> None:
        self.run("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, changed_at = now()",
                 (key, Jsonb(value)))

    def setting_delete(self, key: str) -> None:
        self.run("DELETE FROM settings WHERE key = %s", (key,))

    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM kv WHERE key = %s", (key,))
        return r["value"] if r else default

    def kv_set(self, key: str, value: Any) -> None:
        self.run("INSERT INTO kv (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, Jsonb(value)))

    def audit(self, kind: str, detail: str = "") -> None:
        self.run("INSERT INTO audit (kind, detail) VALUES (%s, %s)", (kind, detail[:500]))
        self.run("DELETE FROM audit WHERE at < now() - interval '180 days'")

    def audit_recent(self, limit: int = 50) -> list[dict]:
        return self.q("SELECT at, kind, detail FROM audit ORDER BY id DESC LIMIT %s", (limit,))

    # ---- sources and feeds -----------------------------------------------------------------------
    def upsert_source(self, s: dict) -> None:
        self.run("""INSERT INTO sources (key, name, domain, country, type, scope) VALUES (%(key)s, %(name)s, %(domain)s, %(country)s, %(type)s, %(scope)s)
                    ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, domain = EXCLUDED.domain, country = EXCLUDED.country,
                    type = EXCLUDED.type, scope = EXCLUDED.scope""", s)

    def upsert_feed(self, f: dict) -> None:
        self.run("""INSERT INTO feeds (source_key, url, topics, places, place_mode, poll_interval_s, next_poll_at, kind)
                    VALUES (%(source_key)s, %(url)s, %(topics)s, %(places)s, %(place_mode)s, %(interval)s, now() + (random() * interval '5 minutes'), %(kind)s)
                    ON CONFLICT (url) DO UPDATE SET source_key = EXCLUDED.source_key, topics = EXCLUDED.topics,
                    places = EXCLUDED.places, place_mode = EXCLUDED.place_mode, kind = EXCLUDED.kind""", {"kind": "rss", **f})

    def list_sources(self) -> list[dict]:
        return self.q("""SELECT s.key, s.name, s.domain, s.country, s.type, s.scope, s.provisional, s.curated, s.score, s.confidence, s.band, s.visible, s.owner,
                         s.override_band, s.trust_updated_at, s.trust_note, s.status, (SELECT count(*) FROM feeds f WHERE f.source_key = s.key) AS feeds,
                         (SELECT count(*) FROM feeds f WHERE f.source_key = s.key AND f.state = 'active') AS feeds_active,
                         (SELECT max(last_ok_at) FROM feeds f WHERE f.source_key = s.key) AS last_ok_at
                         FROM sources s ORDER BY s.country = 'IN' DESC, s.name""")

    def source_get(self, key: str) -> dict | None:
        return self.one("SELECT * FROM sources WHERE key = %s", (key,))

    def source_feeds(self, key: str) -> list[dict]:
        return self.q("SELECT id, url, topics, places, state, last_status, last_error, last_ok_at, last_new_at, fail_count, items_total, poll_interval_s FROM feeds WHERE source_key = %s ORDER BY id", (key,))

    def all_feeds(self) -> list[dict]:
        return self.q("""SELECT f.*, s.type AS source_type, s.scope AS source_scope, s.country AS source_country, s.name AS source_name, s.language AS source_language,
                                s.domain AS source_domain
                         FROM feeds f JOIN sources s ON s.key = f.source_key
                         WHERE s.blocked = false AND f.state NOT IN ('blocked', 'pending', 'retired') AND s.status = ANY(%s) ORDER BY f.id""", (list(POLLABLE),))

    def feeds_due(self, languages: list[str] | None = None) -> list[dict]:
        """Feeds whose time has come, judged by the database clock (one clock for scheduling, however the app is run).
        Only sources in a pollable stage and in an enabled language are polled."""
        return self.q("""SELECT f.*, s.type AS source_type, s.scope AS source_scope, s.country AS source_country, s.name AS source_name, s.language AS source_language,
                                s.domain AS source_domain
                         FROM feeds f JOIN sources s ON s.key = f.source_key
                         WHERE s.blocked = false AND s.status = ANY(%(st)s) AND f.state NOT IN ('blocked', 'pending', 'retired')
                         AND (%(langs)s::text[] IS NULL OR s.language = ANY(%(langs)s) OR s.type = 'social') AND f.next_poll_at <= now() ORDER BY f.next_poll_at""",
                      {"st": list(POLLABLE), "langs": languages})

    def sources_for_sites(self, sites: list[str]) -> dict[str, dict]:
        """{site: the collected source of that site} for article hosts, matching the most specific domain (timesofindia.indiatimes.com
        before indiatimes.com). The dict has the fields IngestService.process_items reads from a feed, with no feed of its own."""
        want: dict[str, list[str]] = {}
        for h in sites:
            parts = h.split(".")
            want[h] = [".".join(parts[i:]) for i in range(len(parts) - 1)]
        doms = sorted({d for v in want.values() for d in v})
        if not doms:
            return {}
        rows = self.q("""SELECT DISTINCT ON (domain) key, domain, type, scope, country, name, language FROM sources
                         WHERE domain = ANY(%s) AND blocked = false AND status = ANY(%s) AND type <> 'social'
                         ORDER BY domain, curated DESC, key""", (doms, list(POLLABLE)))
        by = {r["domain"]: r for r in rows}
        out = {}
        for h, cands in want.items():
            r = next((by[d] for d in cands if d in by), None)
            if r:
                out[h] = {"id": None, "source_key": r["key"], "source_type": r["type"], "source_scope": r["scope"], "source_country": r["country"],
                          "source_name": r["name"], "source_language": r["language"], "topics": [], "places": [], "place_mode": "inherit",
                          "source_domain": r["domain"]}
        return out

    # ---- citations (trust evidence) ---------------------------------------------------------------
    def source_links_add(self, from_key: str, cites: dict[str, int], kind: str) -> None:
        """Count that outlet ``from_key`` cited these sites (registrable domains), ``kind`` 'link' (feed text), 'page' (article page)
        or 'mention' (named as a source in its headline or summary)."""
        if not cites:
            return
        with self.pool.connection() as c:
            c.cursor().executemany("""INSERT INTO source_links (from_key, to_domain, kind, n) VALUES (%s, %s, %s, %s)
                                      ON CONFLICT (from_key, to_domain, kind) DO UPDATE SET n = source_links.n + EXCLUDED.n, last_at = now()""",
                                   [(from_key, d, kind, int(n)) for d, n in cites.items()])

    def source_links_replace(self, kind: str, rows: list[tuple[str, str, int]]) -> None:
        with self.pool.connection() as c:
            c.execute("DELETE FROM source_links WHERE kind = %s", (kind,))
            c.cursor().executemany("INSERT INTO source_links (from_key, to_domain, kind, n) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                   [(a, d, kind, n) for a, d, n in rows])

    def source_links_to(self, days: int = 90) -> list[dict]:
        """Every recorded citation of a site by a source, recent ones only."""
        return self.q("""SELECT l.from_key, l.to_domain, sum(l.n) AS n, array_agg(DISTINCT l.kind) AS kinds FROM source_links l
                         WHERE l.last_at > now() - make_interval(days => %s) GROUP BY l.from_key, l.to_domain""", (days,))

    def source_links_prune(self, days: int = 120) -> int:
        return self.run("DELETE FROM source_links WHERE last_at < now() - make_interval(days => %s)", (days,))

    def rated_article_texts(self, keys: list[str], days: int, limit: int = 400_000):
        """Headlines and summaries of articles by these sources (for finding the outlets they name as a source)."""
        with self.pool.connection() as c, c.cursor(name="rated_texts") as cur:
            cur.itersize = 5000
            cur.execute("""SELECT source_key, title, left(lead, 300) AS lead FROM articles WHERE source_key = ANY(%s) AND seen_day >= current_date - %s LIMIT %s""",
                        (keys, days, limit))
            yield from cur

    # ---- Google News links and home pages ---------------------------------------------------------
    def gnews_links_seen(self, gids: list[str]) -> set[str]:
        if not gids:
            return set()
        return {r["gid"] for r in self.q("SELECT gid FROM gnews_links WHERE gid = ANY(%s)", (gids,))}

    def gnews_links_add(self, rows: list[tuple[str, str | None]]) -> None:
        if not rows:
            return
        with self.pool.connection() as c, c.cursor() as cur:
            cur.executemany("INSERT INTO gnews_links (gid, url) VALUES (%s, %s) ON CONFLICT (gid) DO NOTHING", rows)
            cur.execute("DELETE FROM gnews_links WHERE at < now() - interval '10 days'")

    def recent_titles(self, pairs: list[tuple[str, str]], days: int = 3) -> set[tuple[str, str]]:
        """Of (source key, headline) pairs, those whose source stored an article with that headline (any case) in the last ``days``."""
        if not pairs:
            return set()
        keys = sorted({k for k, _ in pairs})
        titles = sorted({t.strip().lower() for _, t in pairs})
        rows = self.q("""SELECT DISTINCT source_key, lower(title) AS t FROM articles WHERE seen_day >= current_date - %s
                         AND source_key = ANY(%s) AND lower(title) = ANY(%s)""", (days, keys, titles))
        return {(r["source_key"], r["t"]) for r in rows}

    def homepage_seen_update(self, feed_id: int, hashes: list[str]) -> set[str]:
        """Record the links now on a home page; returns those not seen there before. Links gone for 14 days are forgotten."""
        with self.pool.connection() as c:
            old = {r["url_hash"] for r in c.execute("SELECT url_hash FROM homepage_seen WHERE feed_id = %s", (feed_id,)).fetchall()}
            with c.cursor() as cur:
                cur.executemany("""INSERT INTO homepage_seen (feed_id, url_hash) VALUES (%s, %s)
                                   ON CONFLICT (feed_id, url_hash) DO UPDATE SET seen_at = now()""", [(feed_id, h) for h in hashes])
                cur.execute("DELETE FROM homepage_seen WHERE feed_id = %s AND seen_at < now() - interval '14 days'", (feed_id,))
        return set(hashes) - old if old else set()

    def feed_polled(self, feed_id: int, *, status: int | None, error: str | None, new_items: int, items_seen: int,
                    etag: str | None, last_modified: str | None, interval_s: int, ok: bool, state: str) -> None:
        self.run("""UPDATE feeds SET last_poll_at = now(), last_status = %s, last_error = %s,
                    etag = COALESCE(%s, etag), last_modified = COALESCE(%s, last_modified),
                    poll_interval_s = %s, next_poll_at = now() + make_interval(secs => %s * (0.9 + random() * 0.2)),
                    fail_count = CASE WHEN %s THEN 0 ELSE fail_count + 1 END,
                    last_ok_at = CASE WHEN %s THEN now() ELSE last_ok_at END,
                    last_new_at = CASE WHEN %s > 0 THEN now() ELSE last_new_at END,
                    items_total = items_total + %s, state = %s WHERE id = %s""",
                 (status, error, etag, last_modified, interval_s, interval_s, ok, ok, new_items, new_items, state, feed_id))

    def feeds_due_count(self, languages: list[str] | None = None) -> int:
        return self.one("""SELECT count(*) AS n FROM feeds f JOIN sources s ON s.key = f.source_key
                           WHERE f.next_poll_at <= now() AND f.state NOT IN ('blocked', 'pending', 'retired') AND s.status = ANY(%s) AND s.blocked = false
                           AND (%s::text[] IS NULL OR s.language = ANY(%s) OR s.type = 'social')""", (list(POLLABLE), languages, languages))["n"]

    def next_poll_at(self, languages: list[str] | None = None):
        return self.one("""SELECT min(f.next_poll_at) AS t FROM feeds f JOIN sources s ON s.key = f.source_key
                           WHERE f.state NOT IN ('blocked', 'pending', 'retired') AND s.status = ANY(%s) AND s.blocked = false
                           AND (%s::text[] IS NULL OR s.language = ANY(%s) OR s.type = 'social')""", (list(POLLABLE), languages, languages))["t"]

    def feeds_reanalyse(self, lo: int, hi: int) -> dict:
        """Set every working feed's check interval again from its last week of articles (about one check per half the usual gap
        between its new articles, within ``lo`` and ``hi`` seconds; a page of links at least 30 minutes; a feed with no new article in the
        week gets ``hi``). Undoes backing-off that no longer fits and speeds up feeds that became busy. Feeds that are failing keep
        their failure schedule; a feed made faster is also brought forward."""
        rows = self.q("""WITH act AS (SELECT feed_id, count(*) AS n, min(seen_at) AS first FROM articles
                                       WHERE seen_day >= current_date - 7 AND feed_id IS NOT NULL GROUP BY feed_id),
                              want AS (SELECT f.id, f.poll_interval_s AS old,
                                              GREATEST(CASE WHEN f.kind = 'homepage' THEN 1800 ELSE 0 END,
                                                LEAST(%(hi)s, GREATEST(%(lo)s, CASE WHEN a.n IS NULL THEN %(hi)s ELSE
                                                  86400.0 / (a.n / GREATEST(0.25, LEAST(7.0, extract(epoch FROM now() - a.first) / 86400.0))) / 2 END)))::int AS iv
                                       FROM feeds f LEFT JOIN act a ON a.feed_id = f.id
                                       WHERE f.state IN ('active', 'degraded') AND f.fail_count = 0 AND f.last_poll_at IS NOT NULL)
                         UPDATE feeds f SET poll_interval_s = w.iv,
                                            next_poll_at = LEAST(f.next_poll_at, now() + make_interval(secs => w.iv * random()))
                         FROM want w WHERE f.id = w.id AND abs(w.iv - w.old) > 0.2 * w.old
                         RETURNING w.old, w.iv""", {"lo": lo, "hi": hi})
        faster = sum(1 for r in rows if r["iv"] < r["old"])
        return {"changed": len(rows), "faster": faster, "slower": len(rows) - faster}

    def feeds_poll_now(self) -> int:
        return self.run("UPDATE feeds SET next_poll_at = now() WHERE state NOT IN ('blocked', 'pending', 'retired')")

    # ---- topic preferences, favourites -----------------------------------------------------------
    def topic_prefs(self) -> dict[str, bool]:
        return {r["code"]: r["enabled"] for r in self.q("SELECT code, enabled FROM topic_prefs")}

    def topic_pref_set(self, code: str, enabled: bool) -> None:
        self.run("INSERT INTO topic_prefs (code, enabled) VALUES (%s, %s) ON CONFLICT (code) DO UPDATE SET enabled = EXCLUDED.enabled", (code, enabled))

    def topic_prefs_seed(self, defaults: dict[str, bool]) -> None:
        with self.pool.connection() as c, c.cursor() as cur:
            cur.executemany("INSERT INTO topic_prefs (code, enabled) VALUES (%s, %s) ON CONFLICT (code) DO NOTHING", list(defaults.items()))
            cur.execute("DELETE FROM topic_prefs WHERE NOT (code = ANY(%s))", (list(defaults),))      # topics that no longer exist
            cur.execute("DELETE FROM favourites WHERE topic <> '' AND NOT (topic = ANY(%s))", (list(defaults),))

    def topic_prefs_reset(self, defaults: dict[str, bool]) -> None:
        self.run("DELETE FROM topic_prefs")
        self.topic_prefs_seed(defaults)

    def favourites(self) -> list[dict]:
        return self.q("SELECT id, place, topic, position FROM favourites ORDER BY position, id")

    def favourite_add(self, place: str, topic: str) -> None:
        self.run("""INSERT INTO favourites (place, topic, position) VALUES (%s, %s, COALESCE((SELECT max(position) + 1 FROM favourites), 0))
                    ON CONFLICT (place, topic) DO NOTHING""", (place, topic))

    def favourite_remove(self, place: str, topic: str) -> None:
        self.run("DELETE FROM favourites WHERE place = %s AND topic = %s", (place, topic))

    # ---- partitions ------------------------------------------------------------------------------
    def ensure_partition(self, day: dt.date) -> None:
        nxt = day + dt.timedelta(days=1)
        self.run(f"CREATE TABLE IF NOT EXISTS {part_name(day)} PARTITION OF articles FOR VALUES FROM ('{day.isoformat()}') TO ('{nxt.isoformat()}')")

    def partitions(self) -> list[dict]:
        rows = self.q("""SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes, c.reltuples::bigint AS approx_rows
                         FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid WHERE i.inhparent = 'articles'::regclass ORDER BY c.relname""")
        for r in rows:
            m = re.fullmatch(r"articles_(\d{4})(\d{2})(\d{2})", r["name"])
            r["day"] = dt.date(int(m[1]), int(m[2]), int(m[3])) if m else None
        return [r for r in rows if r["day"]]

    def drop_partition(self, day: dt.date) -> None:
        self.run(f"DROP TABLE IF EXISTS {part_name(day)}")

    def delete_stories_before(self, cutoff: dt.datetime) -> int:
        """Remove stories none of whose articles are stored any more. A story's times come from publication, which can be a day or more
        before the day an article was stored, so its time alone never decides (deleting by time removed stories whose articles were kept)."""
        return self.run("""DELETE FROM stories s WHERE s.last_update < %s AND s.id NOT IN (SELECT story_id FROM saved)
                           AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.story_id = s.id)""", (cutoff,))

    # ---- clean-up of stories nobody else reported ------------------------------------------------
    _PURGE_WHERE = """s.last_update < now() - (%(h)s * interval '1 hour') AND s.first_seen < now() - (%(h)s * interval '1 hour')
                      AND NOT ('social' = ANY(s.types)) AND NOT EXISTS (SELECT 1 FROM saved v WHERE v.story_id = s.id)
                      AND ((s.outlets <= 1 AND s.indep <= 1 AND NOT EXISTS (SELECT 1 FROM stories t WHERE t.thread_id = COALESCE(s.thread_id, s.id)
                                                                           AND t.id <> s.id AND t.outlets >= 2))
                           OR (%(ads)s AND s.indep <= 1 AND s.title ~* %(ad)s))"""

    def purge_uncorroborated(self, wait_h: float, batch: int = 4000, budget_s: float = 90.0, ads: bool = True) -> dict:
        """Delete, with their articles, the stories no second outlet picked up in ``wait_h`` hours: one outlet reported it, it is in no storyline
        that has a story of two outlets or more, and it is not saved; and the stories with an advertisement's headline (services/spam.py)
        that no second independent outlet wrote. Social posts are never touched. The wait must be longer than the age limit for feed items, so that
        a feed that still shows an item does not store it again. Works in batches until ``budget_s`` seconds are used. Returns the counts."""
        import time
        from app.services.spam import PATTERN
        t0 = time.monotonic()
        out = {"stories": 0, "articles": 0, "ads": 0}
        prm = {"h": float(wait_h), "ad": PATTERN, "ads": bool(ads), "n": batch}
        while time.monotonic() - t0 < budget_s:
            rows = self.q(f"""SELECT s.id, COALESCE(s.thread_id, s.id) AS th, (s.title ~* %(ad)s) AS ad FROM stories s WHERE {self._PURGE_WHERE}
                              ORDER BY s.last_update LIMIT %(n)s""", prm)
            if not rows:
                break
            ids, threads = [r["id"] for r in rows], list({r["th"] for r in rows})
            with self.pool.connection() as c:
                arts = c.execute("DELETE FROM articles WHERE story_id = ANY(%s)", (ids,)).rowcount
                c.execute("DELETE FROM reprints WHERE story_id = ANY(%s)", (ids,))
                n = c.execute("DELETE FROM stories WHERE id = ANY(%s) AND id NOT IN (SELECT story_id FROM saved)", (ids,)).rowcount
                c.execute("""DELETE FROM storyline_titles t WHERE t.thread_id = ANY(%s)
                             AND NOT EXISTS (SELECT 1 FROM stories x WHERE COALESCE(x.thread_id, x.id) = t.thread_id)""", (threads,))
            out["stories"] += n
            out["articles"] += arts
            out["ads"] += sum(1 for r in rows if r["ad"])
            if len(rows) < batch:
                break
        return out

    def purge_pending(self, wait_h: float, ads: bool = True) -> dict:
        """What the clean-up would delete now (``ready``, capped at a million) and the one-outlet stories still inside their wait (``waiting``)."""
        from app.services.spam import PATTERN
        prm = {"h": float(wait_h), "ad": PATTERN, "ads": bool(ads)}
        ready = self.one(f"SELECT count(*) AS n FROM (SELECT 1 FROM stories s WHERE {self._PURGE_WHERE} LIMIT 1000000) x", prm)["n"]
        waiting = self.one("""SELECT count(*) AS n FROM stories s WHERE s.outlets <= 1 AND s.last_update >= now() - (%(h)s * interval '1 hour')
                              AND NOT ('social' = ANY(s.types))""", prm)["n"]
        return {"ready": ready, "waiting": waiting}

    def reprints_prune(self, oldest_day: dt.date) -> int:
        """Reprints of days no longer stored, and of stories that no longer exist."""
        return self.run("""DELETE FROM reprints r WHERE r.seen_day < %s OR NOT EXISTS (SELECT 1 FROM stories s WHERE s.id = r.story_id)""", (oldest_day,))

    def stories_rebuild(self, ids: list[int], reset_vectors: bool = False) -> dict:
        """After articles left some stories (deleted or moved): delete the ones left empty (unless saved), and rebuild the rest from the
        articles they still have: counts, times, tags, countries, and the headline when the article it came from is gone."""
        ids = sorted(set(i for i in ids if i is not None))
        out = {"stories_deleted": 0, "stories_rebuilt": 0}
        for k in range(0, len(ids), 2000):
            chunk = ids[k:k + 2000]
            with self.pool.connection() as c:
                gone = c.execute("""DELETE FROM stories s WHERE s.id = ANY(%s) AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.story_id = s.id)
                                    AND s.id NOT IN (SELECT story_id FROM saved) RETURNING s.id""", (chunk,)).fetchall()
                out["stories_deleted"] += len(gone)
                if gone:
                    c.execute("DELETE FROM reprints WHERE story_id = ANY(%s)", ([g["id"] for g in gone],))
                live = [i for i in chunk if i not in {g["id"] for g in gone}]
                if not live:
                    continue
                # headline, lead and language from the earliest remaining article when the story's own article is no longer there
                c.execute("""WITH f AS (SELECT DISTINCT ON (a.story_id) a.story_id, a.title, a.lead, a.source_key, a.url, a.lang
                                        FROM articles a WHERE a.story_id = ANY(%s) ORDER BY a.story_id, a.published_at, a.id)
                             UPDATE stories s SET title = f.title, lead = COALESCE(f.lead, ''), lead_source = f.source_key, lead_url = f.url, lang = f.lang,
                                    title_en = NULL, embedding = NULL, embed_n = 0
                             FROM f WHERE s.id = f.story_id AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.story_id = s.id AND a.url = s.lead_url)""", (live,))
                c.execute("""WITH t AS (SELECT u.story_id, min(u.p) f, max(u.p) l, array_agg(DISTINCT so.country) cs, array_agg(DISTINCT so.type) ts
                                        FROM (SELECT story_id, source_key, LEAST(published_at, seen_at) p FROM articles WHERE story_id = ANY(%(ids)s)
                                              UNION ALL SELECT story_id, source_key, published_at FROM reprints WHERE story_id = ANY(%(ids)s)) u
                                        JOIN sources so ON so.key = u.source_key GROUP BY u.story_id)
                             UPDATE stories s SET first_seen = t.f, last_update = t.l, countries = t.cs, types = t.ts,
                                    articles = (SELECT count(*) FROM articles a WHERE a.story_id = s.id) FROM t WHERE s.id = t.story_id""", {"ids": live})
                if reset_vectors:
                    c.execute("UPDATE stories SET embedding = NULL, embed_n = 0 WHERE id = ANY(%s)", (live,))
                c.execute("DELETE FROM story_topics WHERE story_id = ANY(%s)", (live,))
                c.execute("DELETE FROM story_places WHERE story_id = ANY(%s)", (live,))
                c.execute("""INSERT INTO story_topics (story_id, code, n) SELECT story_id, t, count(*) FROM articles, unnest(topics) t
                             WHERE story_id = ANY(%s) GROUP BY 1, 2""", (live,))
                c.execute("""INSERT INTO story_places (story_id, code, n) SELECT story_id, t, count(*) FROM articles, unnest(places) t
                             WHERE story_id = ANY(%s) GROUP BY 1, 2""", (live,))
                out["stories_rebuilt"] += len(live)
        return out

    def purge_languages(self, langs: list[str], batch: int = 5000) -> dict:
        """Delete stored articles in languages that are not collected (stories you saved keep theirs), then the stories left empty."""
        total, touched = 0, set()
        while True:
            with self.pool.connection() as c:
                rows = c.execute("""DELETE FROM articles a WHERE (a.id, a.seen_day) IN (
                                        SELECT id, seen_day FROM articles WHERE lang IS NOT NULL AND NOT (lang = ANY(%s))
                                        AND (story_id IS NULL OR story_id NOT IN (SELECT story_id FROM saved)) LIMIT %s)
                                    RETURNING a.story_id""", (langs, batch)).fetchall()
            total += len(rows)
            touched |= {r["story_id"] for r in rows}
            if len(rows) < batch:
                break
        res = self.stories_rebuild(list(touched)) if touched else {"stories_deleted": 0, "stories_rebuilt": 0}
        if total:
            self.recompute_coverage(7)
        return {"articles": total, **res}

    def fold_reprints(self) -> dict:
        """Once: articles that repeat another article of the same story word for word (same text fingerprint) become reprints. The
        article kept is the story's own (its headline came from it) or else the first published."""
        with self.pool.connection() as c:
            c.execute("""CREATE TEMP TABLE fold ON COMMIT DROP AS
                         SELECT a.id, a.seen_day, a.story_id FROM (
                           SELECT a.id, a.seen_day, a.story_id, row_number() OVER (PARTITION BY a.story_id, a.fp ORDER BY (a.url = s.lead_url) DESC, a.published_at, a.id) AS rn
                           FROM articles a JOIN stories s ON s.id = a.story_id WHERE a.fp IS NOT NULL) a WHERE a.rn > 1""")
            c.execute("""INSERT INTO reprints (url_hash, story_id, source_key, fp, url, published_at, seen_day)
                         SELECT a.url_hash, a.story_id, a.source_key, a.fp, a.url, LEAST(a.published_at, a.seen_at), a.seen_day
                         FROM articles a JOIN fold f ON f.id = a.id AND f.seen_day = a.seen_day ON CONFLICT DO NOTHING""")
            ids = [r["story_id"] for r in c.execute("SELECT DISTINCT story_id FROM fold").fetchall()]
            n = c.execute("DELETE FROM articles a USING fold f WHERE a.id = f.id AND a.seen_day = f.seen_day").rowcount
        res = self.stories_rebuild(ids) if ids else {}
        if n:
            self.recompute_coverage(7)
        return {"folded": n, **res}

    # ---- sizes -----------------------------------------------------------------------------------
    def db_bytes(self) -> int:
        return self.one("SELECT pg_database_size(current_database()) AS b")["b"]

    def wal_bytes(self) -> int | None:
        try:
            return int(self.one("SELECT COALESCE(sum(size), 0)::bigint AS b FROM pg_ls_waldir()")["b"])
        except psycopg.Error:
            return None

    def table_bytes(self) -> dict[str, int]:
        rows = self.q("""SELECT relname, pg_total_relation_size(oid) AS b FROM pg_class
                         WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace AND relname NOT LIKE 'articles\\_%%'""")
        return {r["relname"]: r["b"] for r in rows}

    # ---- ingest ----------------------------------------------------------------------------------
    def existing_hashes(self, hashes: list[str], since_day: dt.date) -> set[str]:
        if not hashes:
            return set()
        return {r["url_hash"] for r in self.q("""SELECT url_hash FROM articles WHERE seen_day >= %s AND url_hash = ANY(%s)
                                                UNION SELECT url_hash FROM reprints WHERE url_hash = ANY(%s)""", (since_day, hashes, hashes))}

    def story_window(self, conn: psycopg.Connection, sid: int) -> dict | None:
        return conn.execute("SELECT first_seen, COALESCE(thread_id, id) AS thread_id FROM stories WHERE id = %s", (sid,)).fetchone()

    def story_by_fingerprint(self, conn: psycopg.Connection, fp: str, since_day: dt.date) -> dict | None:
        """The story an article with exactly this text already belongs to (wire copy printed by many outlets), with its times and thread."""
        return conn.execute("""SELECT a.story_id, s.first_seen, s.last_update, COALESCE(s.thread_id, s.id) AS thread_id FROM articles a JOIN stories s ON s.id = a.story_id
                               WHERE a.fp = %s AND a.seen_day >= %s ORDER BY a.id LIMIT 1""", (fp, since_day)).fetchone()

    def story_title_twin(self, conn: psycopg.Connection, story_id: int, key: str, since_day: dt.date) -> str | None:
        """The text fingerprint of an article of this story with the same headline word for word (``key`` from textutil.norm_title)."""
        if not key:
            return None
        r = conn.execute("""SELECT COALESCE(fp, url_hash) AS fp FROM articles WHERE story_id = %s AND seen_day >= %s
                            AND left(regexp_replace(lower(title), '[^[:alnum:]]+', '', 'g'), 400) = %s ORDER BY id LIMIT 1""",
                         (story_id, since_day, key[:400])).fetchone()
        return r["fp"] if r else None

    def ingest_reprint(self, conn: psycopg.Connection, r: dict) -> tuple[int | None, int]:
        """Record that an outlet carried a story word for word (no article row). Returns (story id, independent outlets); (None, 0) when the
        story is gone. The outlet counts once in the story's outlets; its copy shares the original's text group, so independence is unchanged."""
        if not conn.execute("SELECT 1 FROM stories WHERE id = %s", (r["story_id"],)).fetchone():
            return None, 0
        conn.execute("""INSERT INTO reprints (url_hash, story_id, source_key, fp, url, published_at, seen_day)
                        VALUES (%(url_hash)s, %(story_id)s, %(source_key)s, %(fp)s, %(url)s, %(published_at)s, %(seen_day)s) ON CONFLICT DO NOTHING""", r)
        row = conn.execute(STORY_COUNTS + """
                        UPDATE stories SET last_update = GREATEST(last_update, %(pub)s), """ + STORY_COUNTS_SET + """,
                        countries = (SELECT array(SELECT DISTINCT unnest(countries || ARRAY[(SELECT country FROM sources WHERE key = %(src)s)]))),
                        types = (SELECT array(SELECT DISTINCT unnest(types || ARRAY[(SELECT type FROM sources WHERE key = %(src)s)])))
                        WHERE id = %(sid)s RETURNING indep""",
                           {"sid": r["story_id"], "floor": r["seen_day"] - dt.timedelta(days=6), "pub": r["published_at"], "src": r["source_key"]}).fetchone()
        return r["story_id"], (row["indep"] if row else 0)

    def story_candidates(self, conn: psycopg.Connection, tokens: list[str], since: dt.datetime, limit: int = 40, rare: list[str] | None = None) -> list[dict]:
        """Stories of the last days that may be the same event as a report with these headline words. Uses the caller's connection: asking
        the pool for a second one while holding one can deadlock the pool.

        A word used by thousands of stories (a name in the news of the day) would make the newest 40 stories sharing it the only candidates, and the
        true match, a few minutes older, would never be looked at. So when the report has rarer words the candidates are the stories that share
        one of those, the ones sharing most of them first; newest first among equals."""
        cols = "id, title, tokens, outlets, articles, lead_source, first_seen, last_update, COALESCE(thread_id, id) AS thread_id"
        if rare and len(rare) >= 2:
            return conn.execute(f"""SELECT {cols} FROM stories WHERE last_update > %s AND tokens && %s
                                    ORDER BY (SELECT count(*) FROM unnest(tokens) x WHERE x = ANY(%s)) DESC, last_update DESC LIMIT %s""",
                                (since, rare, rare, limit)).fetchall()
        return conn.execute(f"""SELECT {cols} FROM stories WHERE last_update > %s AND tokens && %s ORDER BY last_update DESC LIMIT %s""",
                            (since, tokens, limit)).fetchall()

    def frequent_tokens(self, hours: int, bar_of) -> tuple[set[str], int]:
        """Headline words used by more stories of the last ``hours`` than the rarity bar (``bar_of(n_stories)``), and the story count."""
        n = self.one("SELECT count(*) AS n FROM stories WHERE last_update > now() - make_interval(hours => %s)", (hours,))["n"]
        rows = self.q("""SELECT t FROM stories, unnest(tokens) t WHERE last_update > now() - make_interval(hours => %s)
                         GROUP BY t HAVING count(*) > %s""", (hours, bar_of(n)))
        return {r["t"] for r in rows}, n

    def ingest_article(self, conn: psycopg.Connection, a: dict, story_id: int | None, new_story: dict | None, topics: dict[str, float],
                       places: dict[str, float]) -> tuple[int, int]:
        """Insert one article and attach it to a story (creating the story when story_id is None). Returns (story id, independent outlets)."""
        if story_id is not None and not conn.execute("SELECT 1 FROM stories WHERE id = %s", (story_id,)).fetchone():
            story_id = None                      # the story was merged into another since it was found (an older article still points at it)
        if story_id is None:
            story_id = conn.execute(
                """INSERT INTO stories (title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types, lang, routine)
                   VALUES (%(title)s, %(lead)s, %(pub)s, %(pub)s, 0, 0, %(tokens)s, %(source_key)s, %(url)s, ARRAY[%(country)s], ARRAY[%(stype)s], %(lang)s, %(routine)s)
                   RETURNING id""", {"lang": None, "routine": False, **new_story, **a, "pub": min(a["published_at"], a["seen_at"])}).fetchone()["id"]
            # a new development of an existing storyline joins its thread; otherwise it starts its own
            conn.execute("UPDATE stories SET thread_id = COALESCE(%s, id) WHERE id = %s", (new_story.get("thread"), story_id))
        conn.execute("""INSERT INTO articles (seen_day, source_key, feed_id, url, url_hash, title, lead, published_at, seen_at, story_id, topics, places, fp, lang)
                        VALUES (%(seen_day)s, %(source_key)s, %(feed_id)s, %(url)s, %(url_hash)s, %(title)s, %(lead)s, %(published_at)s, %(seen_at)s, %(sid)s, %(topics)s, %(places)s, %(fp)s, %(lang)s)""",
                     {"fp": None, "lang": None, **a, "sid": story_id, "topics": sorted(topics), "places": sorted(places)})
        outlets = conn.execute(STORY_COUNTS + """
                        UPDATE stories SET articles = articles + 1, last_update = GREATEST(last_update, %(pub)s), """ + STORY_COUNTS_SET + """,
                        countries = (SELECT array(SELECT DISTINCT unnest(countries || ARRAY[%(country)s]))),
                        types = (SELECT array(SELECT DISTINCT unnest(types || ARRAY[%(stype)s]))),
                        lead = CASE WHEN lead = '' THEN %(lead)s ELSE lead END WHERE id = %(sid)s RETURNING indep""",
                     {**a, "sid": story_id, "floor": (a["seen_at"] - dt.timedelta(days=6)).date(), "pub": min(a["published_at"], a["seen_at"])}).fetchone()["indep"]
        cur = conn.cursor()
        if topics:
            cur.executemany("INSERT INTO story_topics (story_id, code, n) VALUES (%s, %s, 1) ON CONFLICT (story_id, code) DO UPDATE SET n = story_topics.n + 1",
                            [(story_id, c) for c in topics])
        if places:
            cur.executemany("INSERT INTO story_places (story_id, code, n) VALUES (%s, %s, 1) ON CONFLICT (story_id, code) DO UPDATE SET n = story_places.n + 1",
                            [(story_id, c) for c in places])
        return story_id, outlets

    def articles_for_retag(self, after_id: int, limit: int, days: int) -> list[dict]:
        return self.q("""SELECT a.id, a.seen_day, a.title, a.lead, COALESCE(f.topics, '{}') AS topics, COALESCE(f.places, '{}') AS places,
                                COALESCE(f.place_mode, 'inherit') AS place_mode, s.country AS source_country, s.type AS source_type, s.scope AS source_scope,
                                s.language AS source_language
                         FROM articles a JOIN sources s ON s.key = a.source_key LEFT JOIN feeds f ON f.id = a.feed_id
                         WHERE a.id > %s AND a.seen_day >= current_date - %s ORDER BY a.id LIMIT %s""", (after_id, days, limit))

    def article_feeds(self, days: int) -> list[int]:
        return [r["feed_id"] for r in self.q("SELECT DISTINCT feed_id FROM articles WHERE seen_day >= current_date - %s AND feed_id IS NOT NULL", (days,))]

    def feed_article_leads(self, feed_id: int, days: int) -> list[dict]:
        return self.q("SELECT id, seen_day, title, lead FROM articles WHERE feed_id = %s AND seen_day >= current_date - %s ORDER BY id", (feed_id, days))

    def leads_set(self, rows: list[tuple[str, int, dt.date]]) -> None:
        """rows: (lead, id, seen_day). Stories whose own article changed take the new lead too."""
        if not rows:
            return
        with self.pool.connection() as c:
            c.cursor().executemany("UPDATE articles SET lead = %s WHERE id = %s AND seen_day = %s", rows)
            c.execute("""UPDATE stories s SET lead = a.lead FROM articles a WHERE a.id = ANY(%s) AND a.story_id = s.id AND a.url = s.lead_url
                         AND s.lead IS DISTINCT FROM a.lead""", ([r[1] for r in rows],))

    def articles_retagged(self, rows: list[tuple]) -> int:
        """rows: (topics, places, fp, lang, id, seen_day). Rebuilds the topic and place counts of the stories those articles belong to,
        and sets each story's language from its first article."""
        with self.pool.connection() as c:
            c.cursor().executemany("UPDATE articles SET topics = %s, places = %s, fp = %s, lang = %s WHERE id = %s AND seen_day = %s", rows)
            ids = [r["story_id"] for r in c.execute("SELECT DISTINCT story_id FROM articles WHERE id = ANY(%s) AND story_id IS NOT NULL", ([r[4] for r in rows],)).fetchall()]
            if not ids:
                return 0
            c.execute("DELETE FROM story_topics WHERE story_id = ANY(%s)", (ids,))
            c.execute("DELETE FROM story_places WHERE story_id = ANY(%s)", (ids,))
            c.execute("""INSERT INTO story_topics (story_id, code, n) SELECT story_id, t, count(*) FROM articles, unnest(topics) t
                         WHERE story_id = ANY(%s) AND story_id IN (SELECT id FROM stories) GROUP BY 1, 2""", (ids,))
            c.execute("""INSERT INTO story_places (story_id, code, n) SELECT story_id, t, count(*) FROM articles, unnest(places) t
                         WHERE story_id = ANY(%s) AND story_id IN (SELECT id FROM stories) GROUP BY 1, 2""", (ids,))
            c.execute("""UPDATE stories s SET lang = (SELECT a.lang FROM articles a WHERE a.story_id = s.id ORDER BY a.published_at LIMIT 1)
                         WHERE s.id = ANY(%s)""", (ids,))
            return len(ids)

    def split_developments(self, dev_hours: int, tokens_of, days: int = 5) -> dict:
        """Once, after the thread model arrived: a story whose reports span more than ``dev_hours`` is cut, in publication order, into
        developments that each start at the first report not within ``dev_hours`` of the current one. The first keeps the story id;
        the rest become new stories in its thread. Their counts and tags are rebuilt afterwards (retag and coverage passes)."""
        rows = self.q("""SELECT a.id, a.seen_day, a.story_id, LEAST(a.published_at, a.seen_at) AS pub, a.title, a.lead, a.url, a.source_key, a.lang,
                                so.country, so.type
                         FROM articles a JOIN sources so ON so.key = a.source_key
                         WHERE a.seen_day >= current_date - %s AND a.story_id IN (
                             SELECT story_id FROM articles WHERE seen_day >= current_date - %s AND story_id IS NOT NULL GROUP BY story_id
                             HAVING max(LEAST(published_at, seen_at)) - min(LEAST(published_at, seen_at)) > make_interval(hours => %s))
                         ORDER BY a.story_id, pub, a.id""", (days, days, dev_hours))
        by: dict[int, list[dict]] = {}
        for r in rows:
            by.setdefault(r["story_id"], []).append(r)
        win = dt.timedelta(hours=dev_hours)
        made = 0
        with self.pool.connection() as c:
            for sid, arts in by.items():
                groups: list[list[dict]] = [[arts[0]]]
                for a in arts[1:]:
                    if a["pub"] - groups[-1][0]["pub"] > win:
                        groups.append([a])
                    else:
                        groups[-1].append(a)
                if len(groups) < 2:
                    continue
                c.execute("UPDATE stories SET thread_id = %s, embedding = NULL, embed_n = 0, breaking_until = NULL WHERE id = %s", (sid, sid))
                for g in groups[1:]:
                    f = g[0]
                    new = c.execute("""INSERT INTO stories (title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types,
                                                            lang, thread_id)
                                       VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                                    (f["title"], f["lead"] or "", f["pub"], max(x["pub"] for x in g), len(g), tokens_of(f["title"]), f["source_key"], f["url"],
                                     sorted({x["country"] for x in g}), sorted({x["type"] for x in g}), f["lang"], sid)).fetchone()["id"]
                    for x in g:
                        c.execute("UPDATE articles SET story_id = %s WHERE id = %s AND seen_day = %s", (new, x["id"], x["seen_day"]))
                    made += 1
                g0 = groups[0]
                c.execute("""UPDATE stories SET first_seen = %s, last_update = %s, articles = %s, countries = %s, types = %s WHERE id = %s""",
                          (g0[0]["pub"], max(x["pub"] for x in g0), len(g0), sorted({x["country"] for x in g0}), sorted({x["type"] for x in g0}), sid))
            c.execute("SELECT 1")
        return {"threads": sum(1 for v in by.values()), "developments_added": made}

    def stories_restore_orphans(self, tokens_of) -> int:
        """Re-create story rows for stored articles whose story was deleted (keeping the story ids, so earlier merges stand).
        Counts, tags and coverage are rebuilt afterwards by the retag pass."""
        with self.pool.connection() as c:
            rows = c.execute("""SELECT DISTINCT ON (a.story_id) a.story_id, a.title, a.lead, a.source_key, a.url, a.lang, so.country, so.type
                                FROM articles a JOIN sources so ON so.key = a.source_key
                                WHERE a.story_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM stories s WHERE s.id = a.story_id)
                                ORDER BY a.story_id, a.published_at""").fetchall()
            if not rows:
                return 0
            c.cursor().executemany("""INSERT INTO stories (id, title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types, lang)
                                      VALUES (%s, %s, %s, now(), now(), 0, 0, %s, %s, %s, ARRAY[%s], ARRAY[%s], %s) ON CONFLICT (id) DO NOTHING""",
                                   [(r["story_id"], r["title"], r["lead"] or "", tokens_of(r["title"]), r["source_key"], r["url"], r["country"], r["type"], r["lang"]) for r in rows])
            c.execute("""WITH t AS (SELECT a.story_id, min(LEAST(a.published_at, a.seen_at)) f, max(LEAST(a.published_at, a.seen_at)) l, count(*) n,
                                           array_agg(DISTINCT so.country) cs, array_agg(DISTINCT so.type) ts
                                    FROM articles a JOIN sources so ON so.key = a.source_key WHERE a.story_id = ANY(%s) GROUP BY a.story_id)
                         UPDATE stories s SET first_seen = t.f, last_update = t.l, articles = t.n, countries = t.cs, types = t.ts FROM t WHERE s.id = t.story_id""",
                      ([r["story_id"] for r in rows],))
        return len(rows)

    def stories_mark_routine(self, days: int, is_routine) -> int:
        """Set the routine flag of recent stories (after the rule changed): routine when its headline is, or when most of its articles are
        (a cluster of local weather forecasts formed before the rule existed)."""
        rows = self.q("SELECT id, title FROM stories WHERE last_update > now() - make_interval(days => %s)", (days,))
        on = {r["id"] for r in rows if is_routine(r["title"])}
        tally: dict[int, list[int]] = {}
        for a in self.q("""SELECT a.story_id, a.title FROM articles a JOIN stories s ON s.id = a.story_id
                           WHERE s.articles >= 3 AND s.last_update > now() - make_interval(days => %s) AND a.seen_day >= current_date - %s""", (days, days)):
            t = tally.setdefault(a["story_id"], [0, 0])
            t[0] += 1 if is_routine(a["title"]) else 0
            t[1] += 1
        on |= {sid for sid, (k, n) in tally.items() if k * 2 >= n}
        on = sorted(on)
        self.run("UPDATE stories SET routine = (id = ANY(%s)) WHERE last_update > now() - make_interval(days => %s) AND routine <> (id = ANY(%s))", (on, days, on))
        return len(on)

    def mark_breaking(self, story_id: int, outlets: int, minutes: int, mark_hours: int) -> bool:
        """Mark a story Breaking when it first appeared within ``mark_hours`` and ``outlets`` independent outlets (one per source, wire copies
        count once, three or more outlets stamped with the very same instant count once (a scheduled syndicated post), social posts do not count) published it within ``minutes`` of the first publication. The mark lasts until first publication + mark_hours."""
        return self.run("""UPDATE stories s SET breaking_until = fp.first_pub + make_interval(hours => %(h)s)
                           FROM (SELECT min(published_at) AS first_pub FROM articles
                                 WHERE story_id = %(id)s AND seen_day >= (now() - interval '5 days')::date) fp
                           WHERE s.id = %(id)s AND fp.first_pub > now() - make_interval(hours => %(h)s)
                           AND (s.breaking_until IS NULL OR s.breaking_until < now())
                           AND (SELECT COALESCE(sum(CASE WHEN k.c >= 3 THEN 1 ELSE k.c END), 0) FROM (
                                  SELECT x.pa, count(*) AS c FROM (
                                    SELECT DISTINCT ON (g.grp) g.grp, g.pa FROM (
                                      SELECT DISTINCT ON (a.source_key) COALESCE(a.fp, a.source_key) AS grp, a.published_at AS pa
                                      FROM articles a JOIN sources so ON so.key = a.source_key AND so.visible AND so.type <> 'social'
                                      WHERE a.story_id = s.id AND a.seen_day >= (now() - interval '5 days')::date
                                      AND a.published_at <= fp.first_pub + make_interval(mins => %(m)s) ORDER BY a.source_key, a.published_at) g
                                    ORDER BY g.grp, g.pa) x GROUP BY x.pa) k) >= %(n)s""",
                        {"h": mark_hours, "id": story_id, "m": minutes, "n": outlets}) > 0

    # ---- reading stories -------------------------------------------------------------------------
    @staticmethod
    def _scope_cond(place: str, topic: str, i: int, params: dict) -> str:
        parts = []
        if place and place != "world":
            params[f"pl{i}"] = list(place) if isinstance(place, (list, tuple)) else [place]      # a group's scope: the group and its members
            parts.append(f"EXISTS (SELECT 1 FROM story_places x WHERE x.story_id = s.id AND x.code = ANY(%(pl{i})s) AND x.n * 2 >= s.articles)")
        if topic:
            params[f"tp{i}"] = list(topic) if isinstance(topic, (list, tuple)) else [topic]          # several topics: any of them
            parts.append(f"EXISTS (SELECT 1 FROM story_topics y WHERE y.story_id = s.id AND y.code = ANY(%(tp{i})s) AND y.n * 3 >= s.articles AND NOT y.rejected)")
        return " AND ".join(parts) or "TRUE"

    _ROW = """s.id, s.title, s.lead, s.first_seen, s.last_update, s.outlets, s.indep, s.articles, s.lead_source, s.lead_url, s.title_en, s.lang,
              COALESCE(s.breaking_until > now(), false) AS breaking, s.types, s.fair, COALESCE(s.thread_id, s.id) AS thread_id,
              EXISTS (SELECT 1 FROM saved v WHERE v.story_id = s.id) AS is_saved"""

    def list_stories(self, *, tab: str, scopes: list[tuple], min_outlets: int, allow_single: bool, trending_h: float = 2, exclude_types: list[str],
                     langs: list[str] | None = None, limit: int, offset: int = 0, view: str = "all", fresh_h: float = 24, decay_h: float = 6,
                     gravity: float = 2.0, base_h: float = 24, at: dt.datetime | None = None, cand: int = 400, cand2: int = 1500,
                     story_decay_h: float = 6.0, story_gravity: float = 0.0,
                     latest_hours: float = 24, latest_min: int = 3, latest_gap_min: float = 3, latest_sure: int = 10, recent_h: float = 6, **_ignored) -> list[dict]:
        """The candidates of one list, with the numbers its ranking is made of (services/ranking.py makes the order).

        A story is *fresh* when its event was first reported within the last ``fresh_h`` hours (a chain of stories cut by the story-length limit counts
        from its first part), *ongoing* when it began earlier and is still being reported. ``view`` keeps one of the two or both; each side is
        read for its own best ``limit`` rows, so the biggest ongoing stories can not push every fresh one out of the candidates.

        top       ``score`` is the app's fair coverage (STORY_COUNTS: an outlet once, a wire-copy group once, each country's outlets adding as
                  log2(1 + weight)) of the reports published in the last ``fresh_h`` hours, each outlet counted by when it joined:
                  (1 + age / decay_h) ** -gravity, so an outlet that joined an hour ago counts for more than one that joined yesterday.
        trending  the outlets that joined in the last ``trending_h`` hours: ``arrivals_now``, and ``arrivals_base``, the usual number per such
                  window over the ``base_h`` hours before it.
        latest    (kept for the API) every story, newest first report first.
        saved     the stories you saved, newest first.

        ``at`` reads the lists as they were at that time (scripts/replay_lists.py)."""
        now = at or dt.datetime.now(UTC)
        p: dict[str, Any] = {"min": min_outlets, "excl": exclude_types, "lim": limit, "off": offset, "langs": langs, "now": now}
        if tab == "saved":
            return self.q(f"""SELECT {self._ROW}, 0.0 AS score, 1 AS devs, s.first_seen AS since FROM stories s
                              WHERE s.id IN (SELECT story_id FROM saved) ORDER BY (SELECT saved_at FROM saved WHERE story_id = s.id) DESC
                              LIMIT %(lim)s OFFSET %(off)s""", p)
        conds = [self._scope_cond(pl, tp, i, p) for i, (pl, tp) in enumerate(scopes)] or ["TRUE"]
        base = "(" + " OR ".join(conds) + ") AND NOT (s.types <@ %(excl)s::text[])"
        if langs is not None:
            base += " AND COALESCE(s.lang, 'en') = ANY(%(langs)s)"
        if tab == "latest":
            # Latest: the stories that earned a place within ``latest_hours``, newest first. A story earns it when ``latest_min`` different
            # outlets have reported it (an outlet once, wire copies once) and those reports came at different times: reports inside one stretch
            # of ``latest_gap_min`` minutes count once (posts placed at one moment are how spam networks and scheduled syndication look). A story
            # reported by ``latest_sure`` outlets or more is listed without the timing test. ``qualified_at`` is the moment of the last report needed.
            plain = all(not pl or pl == "world" for pl, _t in scopes) and all(not tp for _p, tp in scopes)
            where = base + (" AND NOT s.routine" if plain else "")      # routine items (filings, scoreboards) only inside a chosen scope
            p.update({"lh": float(latest_hours), "lmin": int(latest_min), "lgap": max(1.0, float(latest_gap_min)) * 60.0, "lsure": int(latest_sure),
                      "lfloor": (now - dt.timedelta(hours=float(latest_hours) + 24 * 4)).date()})
            return self.q(f"""WITH cs AS (SELECT s.id FROM stories s WHERE {where} AND NOT (s.types <@ ARRAY['social']) AND s.indep >= %(lmin)s AND s.first_seen <= %(now)s
                                         AND s.last_update > %(now)s - (%(lh)s * interval '1 hour')),
                              d AS (SELECT DISTINCT ON (a.story_id, a.source_key) a.story_id AS id, COALESCE(a.fp, a.source_key) AS fp, a.published_at AS t
                                    FROM articles a JOIN sources so ON so.key = a.source_key AND so.visible AND so.type <> 'social'
                                    WHERE a.story_id IN (SELECT id FROM cs) AND a.seen_day >= %(lfloor)s AND a.published_at <= %(now)s
                                    ORDER BY a.story_id, a.source_key, a.published_at),
                              g AS (SELECT id, fp, min(t) AS t FROM d GROUP BY 1, 2),
                              g2 AS (SELECT id, t, row_number() OVER (PARTITION BY id ORDER BY t) AS rg FROM g),
                              b AS (SELECT id, floor(extract(epoch FROM t) / %(lgap)s) AS bk, min(t) AS t FROM g GROUP BY 1, 2),
                              r AS (SELECT id, t, row_number() OVER (PARTITION BY id ORDER BY t) AS rn FROM b),
                              q AS (SELECT cs.id, LEAST((SELECT t FROM r WHERE r.id = cs.id AND r.rn = %(lmin)s), (SELECT t FROM g2 WHERE g2.id = cs.id AND g2.rg = %(lsure)s)) AS qat
                                    FROM cs)
                              SELECT {self._ROW}, 0.0 AS score, s.indep AS indep_p, s.outlets AS outlets_p, {self._THREAD_COLS}, q.qat AS qualified_at
                              FROM q JOIN stories s ON s.id = q.id
                              WHERE q.qat IS NOT NULL AND q.qat > %(now)s - (%(lh)s * interval '1 hour')
                              ORDER BY q.qat DESC, s.id DESC LIMIT %(lim)s OFFSET %(off)s""", p)
        ranked = base + " AND NOT (s.types <@ ARRAY['social']) AND NOT s.routine"
        trending = tab == "trending"
        per = float(trending_h) if trending else float(fresh_h)             # the window whose reports are counted
        look = per + (float(base_h) if trending else 0.0)                   # trending also reads the day before, for the usual rate
        p.update({"per": per, "look": look, "fresh": float(fresh_h), "tau": float(decay_h), "grav": float(gravity), "pool": limit,
                  "cand": cand, "cand2": cand2, "rech": float(recent_h), "stau": float(story_decay_h), "sgrav": float(story_gravity), "floor": (now - dt.timedelta(hours=max(look, fresh_h) + 24)).date()})
        # Candidates: the stories with the most coverage in all that had a report in the window, and the stories begun inside the fresh window that
        # have two outlets or more (a big event whose reports were split over many small stories is only found by the second kind; they are put
        # together in services/dedupe.py). The exact numbers are then made for these stories only.
        # A "unit" is a story together with the continuations cut from it by the story-length limit (stories.chain_id, set by link_chains).
        in_period = f"""cs AS ((SELECT s.id FROM stories s WHERE {ranked} AND s.first_seen <= %(now)s AND s.last_update > %(now)s - (%(per)s * interval '1 hour')
                                ORDER BY s.fair DESC LIMIT %(cand)s)
                               UNION (SELECT s.id FROM stories s WHERE {ranked} AND s.first_seen BETWEEN %(now)s - (%(fresh)s * interval '1 hour') AND %(now)s
                                AND s.last_update > %(now)s - (%(per)s * interval '1 hour') AND s.indep >= 2 ORDER BY s.fair DESC LIMIT %(cand2)s)),
                        cu AS (SELECT DISTINCT COALESCE(s.chain_id, s.id) AS u FROM stories s WHERE s.id IN (SELECT id FROM cs)),
                        m AS (SELECT s.id, COALESCE(s.chain_id, s.id) AS u FROM stories s
                               WHERE (s.id IN (SELECT u FROM cu) OR s.chain_id IN (SELECT u FROM cu)) AND s.first_seen <= %(now)s
                                 AND s.last_update > %(now)s - (%(look)s * interval '1 hour')),
                        rep AS (SELECT m.u, a.source_key, a.fp, a.published_at FROM articles a JOIN m ON m.id = a.story_id
                                 WHERE a.seen_day >= %(floor)s AND a.published_at <= %(now)s AND a.published_at > %(now)s - (%(look)s * interval '1 hour')
                                UNION ALL SELECT m.u, r.source_key, r.fp, r.published_at FROM reprints r JOIN m ON m.id = r.story_id
                                 WHERE r.seen_day >= %(floor)s AND r.published_at <= %(now)s AND r.published_at > %(now)s - (%(look)s * interval '1 hour')),
                        d AS (SELECT DISTINCT ON (u, source_key) u, source_key, COALESCE(fp, source_key) AS fp, published_at AS t FROM rep ORDER BY u, source_key, published_at),
                        v AS (SELECT d.u, d.fp, d.t, so.weight, so.country FROM d JOIN sources so ON so.key = d.source_key WHERE so.visible AND so.type <> 'social'),
                        g AS (SELECT u, fp, max(weight) AS w, min(t) AS t, (array_agg(country ORDER BY weight DESC))[1] AS country FROM v GROUP BY u, fp),
                        gp AS (SELECT * FROM g WHERE t > %(now)s - (%(per)s * interval '1 hour')),
                        uf AS (SELECT COALESCE(x.chain_id, x.id) AS u, min(x.first_seen) AS fs FROM stories x
                                WHERE x.id IN (SELECT u FROM cu) OR x.chain_id IN (SELECT u FROM cu) GROUP BY 1),
                        cc AS (SELECT gp.u, gp.country, sum(gp.w * power(1 + extract(epoch FROM %(now)s - gp.t) / 3600.0 / %(tau)s, -%(grav)s)
                                                          * power(1 + GREATEST(extract(epoch FROM gp.t - uf.fs), 0) / 3600.0 / %(stau)s, -%(sgrav)s)) AS w
                               FROM gp JOIN uf ON uf.u = gp.u GROUP BY 1, 2),
                        f AS (SELECT cc.u, sum(ln(1 + cc.w) / ln(2)) AS score, max(gi.n) AS indep_p, max(vo.n) AS outlets_p,
                                     max(gi.n) AS arrivals_now, COALESCE(max(rc.n), 0) AS recent_n,
                                     COALESCE(max(gb.n), 0) * %(per)s / GREATEST(%(look)s - %(per)s, 1) AS arrivals_base FROM cc
                              JOIN (SELECT u, count(*) AS n FROM gp GROUP BY 1) gi ON gi.u = cc.u
                              JOIN (SELECT u, count(*) AS n FROM v WHERE t > %(now)s - (%(per)s * interval '1 hour') GROUP BY 1) vo ON vo.u = cc.u
                              LEFT JOIN (SELECT u, count(*) AS n FROM g WHERE t <= %(now)s - (%(per)s * interval '1 hour') GROUP BY 1) gb ON gb.u = cc.u
                              LEFT JOIN (SELECT u, count(*) AS n FROM gp WHERE t > %(now)s - (%(rech)s * interval '1 hour') GROUP BY 1) rc ON rc.u = cc.u
                              GROUP BY cc.u),
                        lead AS (SELECT DISTINCT ON (y.u) y.u, y.id FROM (SELECT m.u, x.id, x.first_seen, x.fair, max(x.fair) OVER (PARTITION BY m.u) AS top
                                                         FROM m JOIN stories x ON x.id = m.id) y ORDER BY y.u, (y.fair * 2 >= y.top) DESC, y.first_seen DESC)"""
        # the row is the newest part that has at least half the coverage of the biggest part, dated from the first
        unit_cols = f"""{self._ROW}, f.score, f.indep_p, f.outlets_p, f.arrivals_now, f.arrivals_base, f.recent_n, {self._THREAD_COLS}, ch.parts,
                        ch.first_seen AS unit_first_seen, ch.articles AS unit_articles,
                        CASE WHEN ch.first_seen > %(now)s - (%(fresh)s * interval '1 hour') THEN 'fresh' ELSE 'ongoing' END AS bucket"""
        unit_from = """FROM f JOIN lead ON lead.u = f.u JOIN stories s ON s.id = lead.id
                       CROSS JOIN LATERAL (SELECT count(*) AS parts, min(x.first_seen) AS first_seen, sum(x.articles) AS articles FROM stories x
                                           WHERE x.id = f.u OR x.chain_id = f.u) ch"""
        floor_n = "2" if trending else ("1" if allow_single else "%(min)s")
        order = "(x.arrivals_now - x.arrivals_base) / sqrt(x.arrivals_base + 1)" if trending else "x.score"
        only = {"fresh": "WHERE x.bucket = 'fresh'", "ongoing": "WHERE x.bucket = 'ongoing'"}.get(view, "")
        return self.q(f"""WITH {in_period}
                          SELECT * FROM (SELECT x.*, row_number() OVER (PARTITION BY x.bucket ORDER BY {order} DESC, x.fair DESC) AS rn
                                         FROM (SELECT {unit_cols} {unit_from} WHERE f.indep_p >= {floor_n}) x {only}) y
                          WHERE y.rn <= %(pool)s""", p)

    def story_vectors(self, ids: list[int]) -> list[dict]:
        """What the duplicate check of a list needs of its stories: meaning vector (text), headline words, times."""
        return self.q("""SELECT id, title, tokens, first_seen, last_update, embedding::text AS e FROM stories WHERE id = ANY(%s)""", (ids,)) if ids else []

    def link_chains(self, span_h: float, gap_h: float, days: int = 10, min_outlets: int = 10, late_h: float = 2.0, min_words: int = 3, min_cos: float = 0.80) -> int:
        """Mark the stories that are the continuation of another: one event whose reports went on past the story-length limit (``span_h``) is cut
        by the ingest into a second story, which begins with the first report that arrives after the limit. Only that exact situation is a chain:
        the same storyline, the newer story's first report no earlier than the limit (less half an hour) and no more than ``late_h`` later
        (a story with many outlets reports every few minutes; a later start is a different development), the older one a story of
        ``min_outlets`` outlets or more still receiving reports then (within ``gap_h``), and the two headlines say the same thing: ``min_words``
        headline words and at least one name (person, team, place) in common, or meaning vectors alike (cosine >= ``min_cos``). Anything less stays two stories: it is better to show two
        rows of one event than to hide an event behind another (a second layer, services/dedupe.py, folds the clear repeats when a list is made).
        Each continuation gets ``chain_id`` = the first story of its chain (a list shows a chain as one row); the stories of the window that are
        in no chain get none. Returns the number of stories in chains."""
        from app.services.anchors import GENERIC, anchors
        from app.services.textutil import title_key
        rows = self.q("""SELECT b.id AS b, a.id AS a, a.title AS ta, b.title AS tb, 1 - (a.embedding <=> b.embedding) AS cos, a.first_seen AS fa FROM stories b
                         JOIN stories a ON a.thread_id = b.thread_id AND a.id <> b.id AND a.first_seen < b.first_seen
                         WHERE b.last_update > now() - make_interval(days => %(d)s) AND b.thread_id IS NOT NULL AND NOT b.routine AND NOT a.routine
                           AND b.first_seen - a.first_seen BETWEEN make_interval(secs => %(lo)s) AND make_interval(secs => %(hi)s)
                           AND a.outlets >= %(mo)s AND a.last_update >= b.first_seen - make_interval(secs => %(gap)s)
                         ORDER BY b.id, a.first_seen DESC""",
                      {"d": days, "lo": span_h * 3600 - 1800, "hi": (span_h + late_h) * 3600, "gap": gap_h * 3600, "mo": min_outlets})
        prev: dict[int, int] = {}
        for r in rows:
            if r["b"] in prev:
                continue
            wa = {w for w in title_key(r["ta"]) if w not in GENERIC}
            wb = {w for w in title_key(r["tb"]) if w not in GENERIC}
            if (len(wa & wb) >= min_words and anchors(r["ta"]) & anchors(r["tb"])) or (r["cos"] or 0) >= min_cos:
                prev[r["b"]] = r["a"]
        stored = {r["id"]: r["chain_id"] for r in self.q("SELECT id, chain_id FROM stories WHERE id = ANY(%s)", (list(set(prev) | set(prev.values())),))} if prev else {}

        def root(x: int) -> int:
            seen = set()
            while x in prev and x not in seen:
                seen.add(x)
                x = prev[x]
            return stored.get(x) or x                 # a first story that is itself in a chain of an earlier window keeps that chain's id
        chain = {b: root(b) for b in prev}
        with self.pool.connection() as c:
            c.execute("UPDATE stories SET chain_id = NULL WHERE chain_id IS NOT NULL AND last_update > now() - make_interval(days => %s) AND NOT (id = ANY(%s))",
                      (days, list(chain)))
            if chain:
                c.execute("""UPDATE stories s SET chain_id = x.r FROM unnest(%s::bigint[], %s::bigint[]) AS x(i, r)
                             WHERE s.id = x.i AND s.chain_id IS DISTINCT FROM x.r""", (list(chain), list(chain.values())))
        return len(chain)

    _THREAD_COLS = """(SELECT count(*) FROM stories t WHERE t.thread_id = COALESCE(s.thread_id, s.id)) AS devs,
                      (SELECT min(t.first_seen) FROM stories t WHERE t.thread_id = COALESCE(s.thread_id, s.id)) AS since"""

    def fix_date_only_times(self, days: int = 3) -> dict:
        """Reports whose time is only a date (stored as 00:00:00 UTC) and that were seen within a day and a half of it get the time they
        were seen; the first and last report of the stories they belong to are set again. Returns the counts."""
        with self.pool.connection() as c:
            rows = c.execute("""UPDATE articles SET published_at = seen_at
                                WHERE seen_day >= current_date - %s AND (published_at AT TIME ZONE 'UTC')::time = '00:00:00'
                                  AND seen_at >= published_at AND seen_at - published_at < interval '36 hours' RETURNING story_id""", (days,)).fetchall()
            ids = sorted({r["story_id"] for r in rows if r["story_id"]})
            fixed = 0
            for i in range(0, len(ids), 2000):
                fixed += c.execute("""UPDATE stories s SET first_seen = x.f, last_update = GREATEST(s.last_update, x.l)
                                      FROM (SELECT story_id, min(published_at) AS f, max(published_at) AS l FROM """ + COVERAGE_ROWS + """ r
                                            WHERE story_id = ANY(%s) GROUP BY story_id) x WHERE s.id = x.story_id""", (ids[i:i + 2000],)).rowcount
        return {"reports": len(rows), "stories": fixed}

    def thread_developments(self, thread_id: int, limit: int = 40, min_outlets: int = 3, keep: int | None = None) -> tuple[list[dict], int]:
        """The developments of a storyline worth listing - those with ``min_outlets`` outlets or more (and ``keep``, the story being read) - newest
        first, and how many others there are (minor reports of one or two outlets, which would crowd out the developments)."""
        total = int(self.one("SELECT count(*) AS n FROM stories WHERE thread_id = %s", (thread_id,))["n"])
        floor = 0 if total <= 8 else min_outlets                      # a short storyline lists all of its developments
        rows = self.q("""SELECT id, title, title_en, lang, first_seen, last_update, indep, outlets,
                                COALESCE(breaking_until > now(), false) AS breaking
                         FROM stories WHERE thread_id = %s AND (indep >= %s OR id = %s) ORDER BY first_seen DESC LIMIT %s""", (thread_id, floor, keep or 0, limit))
        return rows, max(0, total - len(rows))

    # ---- headlines (services/headlines.py) --------------------------------------------------------
    def token_df(self, hours: int) -> tuple[dict[str, int], int]:
        n = self.one("SELECT count(*) AS n FROM stories WHERE last_update > now() - make_interval(hours => %s)", (hours,))["n"]
        rows = self.q("SELECT t, count(*) AS c FROM stories, unnest(tokens) t WHERE last_update > now() - make_interval(hours => %s) GROUP BY t", (hours,))
        return {r["t"]: r["c"] for r in rows}, n

    _REPORTS = """SELECT a.story_id, a.title, a.lead, LEAST(a.published_at, a.seen_at) AS published, a.url, a.source_key, so.weight,
                         s.title AS story_title, s.lead_url AS story_url
                  FROM articles a JOIN stories s ON s.id = a.story_id JOIN sources so ON so.key = a.source_key AND so.type <> 'social' AND so.visible
                  WHERE {where} AND a.seen_day >= (%(since)s - interval '6 days')::date AND COALESCE(a.lang, 'en') = 'en'"""

    def reports_of_changed_stories(self, since: dt.datetime) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for r in self.q(self._REPORTS.format(where="s.last_update > %(since)s AND s.articles >= 2 AND COALESCE(s.lang, 'en') = 'en'"), {"since": since}):
            out.setdefault(r["story_id"], []).append(r)
        return out

    def story_headlines_set(self, updates: list[tuple[int, dict]]) -> int:
        if not updates:
            return 0
        with self.pool.connection() as c, c.cursor() as cur:
            cur.executemany("""UPDATE stories SET title = %s, lead = %s, lead_url = %s, lead_source = %s, title_en = NULL WHERE id = %s""",
                            [(p["title"], p["lead"], p["url"], p["source_key"], sid) for sid, p in updates])
        return len(updates)

    def reports_of_changed_threads(self, since: dt.datetime, per_thread: int = 300) -> dict[int, list[dict]]:
        """All reports of storylines (two or more developments) in which something changed, newest first, at most ``per_thread``."""
        out: dict[int, list[dict]] = {}
        rows = self.q("""WITH th AS (SELECT thread_id FROM stories WHERE thread_id IN (SELECT thread_id FROM stories WHERE last_update > %(since)s)
                                     GROUP BY thread_id HAVING count(*) >= 2),
                              r AS (SELECT s.thread_id, a.title, a.lead, LEAST(a.published_at, a.seen_at) AS published, a.url, a.source_key, so.weight,
                                           row_number() OVER (PARTITION BY s.thread_id ORDER BY a.published_at DESC) AS k
                                    FROM articles a JOIN stories s ON s.id = a.story_id AND s.thread_id IN (SELECT thread_id FROM th) AND NOT s.routine
                                    JOIN sources so ON so.key = a.source_key AND so.type <> 'social' AND so.visible
                                    WHERE a.seen_day >= (now() - interval '30 days')::date AND COALESCE(a.lang, 'en') = 'en')
                         SELECT * FROM r WHERE k <= %(n)s""", {"since": since, "n": per_thread})
        for r in rows:
            out.setdefault(r["thread_id"], []).append(r)
        return out

    def storyline_titles_set(self, updates: list[tuple[int, dict]]) -> int:
        if not updates:
            return 0
        with self.pool.connection() as c, c.cursor() as cur:
            cur.executemany("""INSERT INTO storyline_titles (thread_id, title, lead, url, source_key) VALUES (%s, %s, %s, %s, %s)
                               ON CONFLICT (thread_id) DO UPDATE SET title = EXCLUDED.title, lead = EXCLUDED.lead, url = EXCLUDED.url,
                               source_key = EXCLUDED.source_key, updated_at = now()""",
                            [(th, p["title"], p["lead"], p["url"], p["source_key"]) for th, p in updates])
        return len(updates)

    # ---- storylines ------------------------------------------------------------------------------
    def list_storylines(self, *, scopes: list[tuple], hours: int, min_devs: int, min_outlets: int, exclude_types: list[str],
                        langs: list[str] | None, limit: int, offset: int) -> list[dict]:
        """Storylines (threads) ranked by the coverage their developments drew in the last ``hours``: the best active
        development's fair coverage plus 0.3 of the rest (a long storyline cannot win by size alone); each development's fair coverage (outlets counted once per text, credited by country with diminishing returns: STORY_COUNTS).
        A development counts when it had a report in the period. A storyline needs ``min_outlets`` independent outlets in the period."""
        p: dict[str, Any] = {"h": hours, "md": min_devs, "min": min_outlets, "excl": exclude_types, "langs": langs, "lim": limit, "off": offset}
        conds = [self._scope_cond(pl, tp, i, p) for i, (pl, tp) in enumerate(scopes)] or ["TRUE"]
        where = "(" + " OR ".join(conds) + ") AND NOT (s.types <@ %(excl)s::text[]) AND NOT (s.types <@ ARRAY['social']) AND NOT s.routine"
        if langs is not None:
            where += " AND COALESCE(s.lang, 'en') = ANY(%(langs)s)"
        return self.q(f"""WITH d AS (SELECT s.id, COALESCE(s.thread_id, s.id) AS th, s.first_seen, s.last_update, s.fair, s.indep, s.countries
                                     FROM stories s WHERE {where} AND s.last_update > now() - make_interval(hours => %(h)s)),
                               t AS (SELECT th, max(fair) + 0.3 * (sum(fair) - max(fair)) AS score, count(*) AS devs_in, sum(indep) AS outlets_in FROM d GROUP BY th HAVING sum(indep) >= %(min)s),
                               tt AS (SELECT thread_id AS th, count(*) AS devs_total, min(first_seen) AS began, max(last_update) AS latest FROM stories
                                      WHERE thread_id IN (SELECT th FROM t) GROUP BY thread_id),
                               ok AS (SELECT t.*, tt.devs_total, tt.began, tt.latest FROM t LEFT JOIN tt ON tt.th = t.th
                                      WHERE GREATEST(COALESCE(tt.devs_total, 0), t.devs_in) >= %(md)s ORDER BY t.score DESC LIMIT %(lim)s OFFSET %(off)s),
                               dc AS (SELECT d.th, count(DISTINCT c) AS countries_in FROM d JOIN ok ON ok.th = d.th, unnest(d.countries) c GROUP BY d.th),
                               rep AS (SELECT DISTINCT ON (d.th) d.th, d.id FROM d JOIN ok ON ok.th = d.th ORDER BY d.th, d.fair DESC, d.last_update DESC),
                               lat AS (SELECT DISTINCT ON (x.thread_id) x.thread_id AS th, x.id, x.title FROM stories x JOIN ok ON ok.th = x.thread_id
                                       WHERE NOT x.routine ORDER BY x.thread_id, x.first_seen DESC)
                          SELECT {self._ROW}, ok.th, ok.score, ok.devs_in, ok.outlets_in, COALESCE(dc.countries_in, 0) AS countries_in,
                                 stt.title AS line_title, stt.lead AS line_lead, stt.source_key AS line_source,
                                 GREATEST(COALESCE(ok.devs_total, 0), ok.devs_in) AS devs_total,
                                 COALESCE(ok.began, s.first_seen) AS began, COALESCE(ok.latest, s.last_update) AS latest, s.id AS rep_id,
                                 lat.id AS latest_id, lat.title AS latest_title
                          FROM ok JOIN rep ON rep.th = ok.th JOIN stories s ON s.id = rep.id LEFT JOIN lat ON lat.th = ok.th LEFT JOIN dc ON dc.th = ok.th
                          LEFT JOIN storyline_titles stt ON stt.thread_id = ok.th AND ok.devs_total >= 2
                          ORDER BY ok.score DESC""", p)

    def thread_developments_many(self, threads: list[int], hours: int, min_outlets: int = 3, per_thread: int = 6) -> dict[int, tuple[list[dict], int]]:
        """For each storyline its biggest developments (``per_thread`` of them with ``min_outlets`` outlets or more, newest first) and the number of others."""
        if not threads:
            return {}
        out: dict[int, list[dict]] = {}
        total: dict[int, int] = {}
        for r in self.q("""SELECT thread_id AS th, id, title, title_en, lang, first_seen, indep, last_update > now() - make_interval(hours => %s) AS in_range
                           FROM stories WHERE thread_id = ANY(%s) AND NOT routine ORDER BY first_seen DESC""", (hours, threads)):
            total[r["th"]] = total.get(r["th"], 0) + 1
            out.setdefault(r["th"], []).append(r)
        res = {}
        for th in threads:
            every = out.get(th, [])
            pool = every if total.get(th, 0) <= per_thread else [d for d in every if d["indep"] >= min_outlets]       # a short storyline lists all
            big = sorted(pool, key=lambda d: -d["indep"])[:per_thread]
            big.sort(key=lambda d: d["first_seen"], reverse=True)
            res[th] = (big, max(0, total.get(th, 0) - len(big)))
        return res

    def thread_activity(self, threads: list[int], hours: int, buckets: int) -> dict[int, list[int]]:
        """Outlets reporting on each storyline per time slice of the period (oldest first): its rise and fall."""
        if not threads:
            return {}
        rows = self.q(f"""SELECT s.thread_id AS th, LEAST(%(b)s - 1, floor(extract(epoch FROM (a.published_at - (now() - make_interval(hours => %(h)s))))
                                  / (%(h)s * 3600.0 / %(b)s)))::int AS k, count(DISTINCT a.source_key) AS n
                          FROM {COVERAGE_ROWS} a JOIN stories s ON s.id = a.story_id
                          WHERE s.thread_id = ANY(%(t)s) AND a.seen_day >= (now() - make_interval(hours => %(h)s + 24))::date
                          AND a.published_at > now() - make_interval(hours => %(h)s) GROUP BY 1, 2""", {"t": threads, "h": hours, "b": buckets})
        out = {t: [0] * buckets for t in threads}
        for r in rows:
            if 0 <= r["k"] < buckets:
                out[r["th"]][r["k"]] = r["n"]
        return out

    # ---- storyline repair ------------------------------------------------------------------------
    def stories_for_repair(self, days: int) -> list[dict]:
        return self.q("""SELECT id, lead_url FROM stories WHERE articles >= 2 AND last_update > now() - make_interval(days => %s)
                         AND COALESCE(lang, 'en') = 'en' ORDER BY id""", (days,))

    def story_article_titles(self, sid: int) -> list[dict]:
        return self.q("SELECT id, seen_day, title, fp, url, published_at FROM articles WHERE story_id = %s ORDER BY published_at, id", (sid,))

    def story_split(self, sid: int, arts: list[dict], tokens: list[str]) -> int | None:
        """Move these articles (and reprints of their text) out of story ``sid`` into a new story of its own storyline."""
        if not arts:
            return None
        f = arts[0]
        with self.pool.connection() as c:
            old = c.execute("SELECT lang, routine FROM stories WHERE id = %s", (sid,)).fetchone()
            if not old:
                return None
            new = c.execute("""INSERT INTO stories (title, lead, first_seen, last_update, outlets, articles, tokens, lead_source, lead_url, countries, types, lang, routine)
                               SELECT a.title, COALESCE(a.lead, ''), LEAST(a.published_at, a.seen_at), LEAST(a.published_at, a.seen_at), 0, 0, %s, a.source_key, a.url,
                                      ARRAY[so.country], ARRAY[so.type], a.lang, %s
                               FROM articles a JOIN sources so ON so.key = a.source_key WHERE a.id = %s AND a.seen_day = %s RETURNING id""",
                            (tokens, old["routine"], f["id"], f["seen_day"])).fetchone()["id"]
            c.execute("UPDATE stories SET thread_id = id WHERE id = %s", (new,))
            c.cursor().executemany("UPDATE articles SET story_id = %s WHERE id = %s AND seen_day = %s", [(new, a["id"], a["seen_day"]) for a in arts])
            fps = [a["fp"] for a in arts if a["fp"]]
            if fps:
                c.execute("UPDATE reprints SET story_id = %s WHERE story_id = %s AND fp = ANY(%s)", (new, sid, fps))
            return new

    def threads_for_repair(self, days: int) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for r in self.q("""SELECT id, title, first_seen, thread_id FROM stories WHERE thread_id IN (
                               SELECT thread_id FROM stories WHERE last_update > now() - make_interval(days => %s) GROUP BY thread_id HAVING count(*) >= 2)
                           AND COALESCE(lang, 'en') = 'en'""", (days,)):
            out.setdefault(r["thread_id"], []).append(r)
        return out

    def thread_assign(self, ids: list[int], thread_id: int) -> int:
        return self.run("UPDATE stories SET thread_id = %s WHERE id = ANY(%s)", (thread_id, ids))

    def scope_count(self, codes: list[str], topic: str, window_h: int, min_outlets: int) -> int:
        """Stories in the window for a scope given as several place codes (a group and its members)."""
        p: dict[str, Any] = {"win": window_h, "min": min_outlets}
        return self.one(f"""SELECT count(*) AS n FROM stories s WHERE s.last_update > now() - make_interval(hours => %(win)s) AND s.indep >= %(min)s
                            AND {self._scope_cond(codes, topic, 0, p)}""", p)["n"]

    def story_sources(self, ids: list[int]) -> dict[int, list[dict]]:
        """Outlets carrying each story, first publisher first. ``grp`` is a fingerprint of the text (outlets with the same text are one
        independent voice); a reprint takes the group of the article it copies."""
        if not ids:
            return {}
        rows = self.q("""WITH x AS (SELECT a.story_id, a.source_key, a.published_at, a.fp,
                                    md5(CASE WHEN length(a.lead) > 60 THEN left(regexp_replace(lower(a.lead), '[^a-z0-9]+', '', 'g'), 100)
                                             ELSE regexp_replace(lower(a.title), '[^a-z0-9]+', '', 'g') END) AS grp
                                    FROM articles a WHERE a.story_id = ANY(%(ids)s)),
                              fg AS (SELECT story_id, fp, min(grp) AS grp FROM x GROUP BY story_id, fp),
                              r AS (SELECT r.story_id, r.source_key, r.published_at, r.fp, fg.grp
                                    FROM reprints r LEFT JOIN fg ON fg.story_id = r.story_id AND fg.fp = r.fp WHERE r.story_id = ANY(%(ids)s)),
                              u AS (SELECT * FROM x UNION ALL SELECT * FROM r)
                         SELECT u.story_id, u.source_key AS key, s.name, s.type, s.country, s.band, s.trust_basis AS basis, min(u.published_at) AS first_at,
                                count(*) AS n, min(u.grp) AS grp
                         FROM u JOIN sources s ON s.key = u.source_key AND s.visible
                         GROUP BY u.story_id, u.source_key, s.name, s.type, s.country, s.band, s.trust_basis ORDER BY min(u.published_at)""", {"ids": ids})
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["story_id"], []).append(r)
        return out

    def story_reprints(self, sid: int) -> list[dict]:
        return self.q("""SELECT r.url, r.published_at, r.source_key, s.name AS source_name, s.type AS source_type, s.country AS source_country,
                                s.band AS source_band, s.trust_basis AS source_basis, s.visible AS source_visible
                         FROM reprints r JOIN sources s ON s.key = r.source_key WHERE r.story_id = %s ORDER BY r.published_at""", (sid,))

    def story_tags(self, ids: list[int]) -> tuple[dict[int, dict[str, int]], dict[int, dict[str, int]]]:
        topics: dict[int, dict[str, int]] = {}
        places: dict[int, dict[str, int]] = {}
        for r in self.q("SELECT story_id, code, n FROM story_topics WHERE story_id = ANY(%s) AND NOT rejected", (ids,)):
            topics.setdefault(r["story_id"], {})[r["code"]] = r["n"]
        for r in self.q("SELECT story_id, code, n FROM story_places WHERE story_id = ANY(%s)", (ids,)):
            places.setdefault(r["story_id"], {})[r["code"]] = r["n"]
        return topics, places

    def story_get(self, sid: int) -> dict | None:
        return self.one("""SELECT s.*, (s.breaking_until > now()) AS breaking, EXISTS (SELECT 1 FROM saved v WHERE v.story_id = s.id) AS is_saved
                           FROM stories s WHERE s.id = %s""", (sid,))

    def story_articles(self, sid: int) -> list[dict]:
        return self.q("""SELECT a.id, a.url, a.title, a.lead, a.published_at, a.seen_at, a.source_key, a.title_en, a.lang, s.name AS source_name, s.type AS source_type,
                         s.country AS source_country, s.domain AS source_domain, s.provisional, s.band AS source_band, s.score AS source_score, s.visible AS source_visible,
                         s.trust_basis AS source_basis
                         FROM articles a JOIN sources s ON s.key = a.source_key WHERE a.story_id = %s ORDER BY a.published_at DESC, a.id DESC""", (sid,))

    def facets(self, *, place: str, topic: str, window_h: int, min_outlets: int) -> tuple[dict[str, int], dict[str, int]]:
        p: dict[str, Any] = {"win": window_h, "min": min_outlets}
        base = "s.last_update > now() - make_interval(hours => %(win)s) AND s.indep >= %(min)s"
        t = self.q(f"""SELECT t.code, count(*) AS n FROM stories s JOIN story_topics t ON t.story_id = s.id AND t.n * 3 >= s.articles AND NOT t.rejected
                       WHERE {base} AND {self._scope_cond(place, '', 0, p)} GROUP BY t.code""", p)
        p2: dict[str, Any] = {"win": window_h, "min": min_outlets}
        pl = self.q(f"""SELECT x.code, count(*) AS n FROM stories s JOIN story_places x ON x.story_id = s.id AND x.n * 2 >= s.articles
                        WHERE {base} AND {self._scope_cond('', topic, 0, p2)} GROUP BY x.code""", p2)
        return {r["code"]: r["n"] for r in t}, {r["code"]: r["n"] for r in pl}

    def saved_set(self, sid: int, on: bool) -> None:
        if on:
            self.run("INSERT INTO saved (story_id) VALUES (%s) ON CONFLICT DO NOTHING", (sid,))
        else:
            self.run("DELETE FROM saved WHERE story_id = %s", (sid,))

    # ---- status ----------------------------------------------------------------------------------
    def counts(self) -> dict:
        r = self.one("""SELECT (SELECT count(*) FROM stories) AS stories, (SELECT count(*) FROM sources) AS sources,
                        (SELECT count(*) FROM feeds) AS feeds, (SELECT count(*) FROM feeds WHERE state = 'active' AND fail_count = 0) AS feeds_ok,
                        (SELECT count(*) FROM feeds WHERE fail_count >= 3) AS feeds_failing,
                        (SELECT max(last_poll_at) FROM feeds) AS last_poll, (SELECT max(last_update) FROM stories) AS last_story""")
        return r or {}

    def article_count_by_day(self) -> list[dict]:
        return self.q("SELECT seen_day, count(*) AS n FROM articles GROUP BY seen_day ORDER BY seen_day")

    # ---- jobs ------------------------------------------------------------------------------------
    def job_create(self, kind: str) -> int:
        return self.one("INSERT INTO jobs (kind) VALUES (%s) RETURNING id", (kind,))["id"]

    def job_update(self, jid: int, *, state: str | None = None, progress: dict | None = None, result: dict | None = None) -> None:
        self.run("""UPDATE jobs SET state = COALESCE(%s, state), progress = COALESCE(%s, progress), result = COALESCE(%s, result),
                    started_at = CASE WHEN %s = 'running' AND started_at IS NULL THEN now() ELSE started_at END,
                    finished_at = CASE WHEN %s IN ('done', 'failed') THEN now() ELSE finished_at END WHERE id = %s""",
                 (state, Jsonb(progress) if progress is not None else None, Jsonb(result) if result is not None else None, state, state, jid))

    def jobs_close_interrupted(self) -> int:
        """A job still marked queued/running when the app starts was cut off by a restart; nothing is running yet."""
        return self.run("""UPDATE jobs SET state = 'failed', finished_at = now(), result = '{"error": "interrupted by a restart"}'::jsonb
                           WHERE state IN ('queued', 'running')""")

    def job_get(self, jid: int) -> dict | None:
        return self.one("SELECT * FROM jobs WHERE id = %s", (jid,))

    def job_last(self, kind: str) -> dict | None:
        return self.one("SELECT * FROM jobs WHERE kind = %s ORDER BY id DESC LIMIT 1", (kind,))

    # ---- trust -----------------------------------------------------------------------------------
    def ref_scores_replace(self, dataset: str, rows: list[tuple]) -> None:
        """rows: (domain, value, label, detail, url)"""
        with self.pool.connection() as c:
            c.execute("DELETE FROM ref_scores WHERE dataset = %s", (dataset,))
            c.cursor().executemany("INSERT INTO ref_scores (dataset, domain, value, label, detail, url) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                   [(dataset, *r) for r in rows])

    def ref_scores_get(self, dataset: str) -> dict[str, dict]:
        return {r["domain"]: r for r in self.q("SELECT domain, value, label, detail, url FROM ref_scores WHERE dataset = %s", (dataset,))}

    def ref_scores_fresh(self, dataset: str, domains: list[str], max_age_days: int) -> dict[str, dict]:
        rows = self.q("SELECT domain, value, label, detail, url FROM ref_scores WHERE dataset = %s AND domain = ANY(%s) AND fetched_at > now() - make_interval(days => %s)",
                      (dataset, domains, max_age_days))
        return {r["domain"]: r for r in rows}

    def ref_score_upsert(self, dataset: str, domain: str, value: float, label: str, detail: str, url: str) -> None:
        self.run("""INSERT INTO ref_scores (dataset, domain, value, label, detail, url) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dataset, domain) DO UPDATE SET value = EXCLUDED.value, label = EXCLUDED.label, detail = EXCLUDED.detail, url = EXCLUDED.url, fetched_at = now()""",
                 (dataset, domain, value, label, detail, url))

    def sources_for_trust(self) -> list[dict]:
        return self.q("""SELECT key, name, domain, type, curated, override_band, band, score, visible, trust_updated_at, owner, language FROM sources
                         WHERE blocked = false AND type <> 'social' AND status = ANY(%s) ORDER BY key""", (list(POLLABLE),))

    def ref_scores_labelled(self, dataset: str) -> bool:
        return bool(self.one("SELECT EXISTS (SELECT 1 FROM ref_scores WHERE dataset = %s AND label <> '') AS x", (dataset,))["x"])

    def ref_scores_fresh_count(self, dataset: str, max_age_days: int) -> int:
        return self.one("SELECT count(*) AS n FROM ref_scores WHERE dataset = %s AND fetched_at > now() - make_interval(days => %s)", (dataset, max_age_days))["n"]

    def corroboration_rated(self, age_hours: int, strong: list[str], days: int = 90) -> dict[str, tuple[int, int]]:
        """For each source: stories it reported first (at least ``age_hours`` old), and how many of them a source in ``strong`` also
        reported with a text of its own (another fingerprint: not a reprint of the first report)."""
        rows = self.q("""WITH firsts AS (
                           SELECT DISTINCT ON (a.story_id) a.story_id, a.source_key, a.fp
                           FROM articles a JOIN stories s ON s.id = a.story_id
                           WHERE a.seen_day >= current_date - %(days)s AND s.first_seen < now() - make_interval(hours => %(age)s)
                           ORDER BY a.story_id, a.published_at, a.id)
                         SELECT f.source_key, count(*) AS n,
                                count(*) FILTER (WHERE EXISTS (SELECT 1 FROM articles a2 WHERE a2.story_id = f.story_id AND a2.source_key <> f.source_key
                                                               AND a2.source_key = ANY(%(strong)s) AND a2.fp IS DISTINCT FROM f.fp
                                                               AND a2.seen_day >= current_date - %(days)s)) AS ok
                         FROM firsts f GROUP BY f.source_key""", {"days": days, "age": age_hours, "strong": strong})
        return {r["source_key"]: (r["n"], r["ok"]) for r in rows}

    def signals_all(self) -> list[dict]:
        return self.q("SELECT source_key, signal, value, factor, weight, detail, evidence_url, evidence_date FROM trust_signals")

    def signals_for(self, key: str) -> list[dict]:
        return self.q("SELECT signal, value, factor, weight, detail, evidence_url, evidence_date, fetched_at FROM trust_signals WHERE source_key = %s ORDER BY weight DESC", (key,))

    def trust_apply(self, key: str, sigs, ev: dict, *, owner: str | None, qid: str | None, touch: bool, reason: str) -> bool:
        """Store one source's evidence (when ``sigs`` is given) and result. Returns True when its band or visibility changed."""
        with self.pool.connection() as c:
            old = c.execute("SELECT band, visible FROM sources WHERE key = %s FOR UPDATE", (key,)).fetchone()
            if sigs is not None:
                c.execute("DELETE FROM trust_signals WHERE source_key = %s", (key,))
                c.cursor().executemany("""INSERT INTO trust_signals (source_key, signal, value, factor, weight, detail, evidence_url, evidence_date)
                                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                       [(key, g.signal, g.value, g.factor, g.weight, g.detail, g.url, g.date) for g in sigs])
            c.execute("""UPDATE sources SET score = %(score)s, confidence = %(confidence)s, band = %(band)s, visible = %(visible)s, weight = %(weight)s, trust_note = %(note)s,
                         trust_basis = %(basis)s,
                         owner = COALESCE(%(owner)s, owner), wikidata_id = COALESCE(%(qid)s, wikidata_id),
                         trust_updated_at = CASE WHEN %(touch)s THEN now() ELSE trust_updated_at END,
                         provisional = CASE WHEN %(touch)s THEN false ELSE provisional END WHERE key = %(key)s""",
                      {"basis": None, **ev, "owner": owner, "qid": qid, "touch": touch, "key": key})
            changed = bool(old) and (old["band"] != ev["band"] or old["visible"] != ev["visible"])
            if changed:
                c.execute("INSERT INTO source_history (source_key, change, reason) VALUES (%s, %s, %s)",
                          (key, f"{old['band']} to {ev['band']}" + ("" if old["visible"] == ev["visible"] else (", now shown" if ev["visible"] else ", now hidden")), reason))
            return changed

    def source_override_set(self, key: str, band: str | None) -> None:
        self.run("UPDATE sources SET override_band = %s WHERE key = %s", (band, key))
        self.run("INSERT INTO source_history (source_key, change, reason) VALUES (%s, %s, 'owner override')", (key, f"override set to {band or 'none'}"))

    def source_history(self, key: str, limit: int = 20) -> list[dict]:
        return self.q("SELECT at, change, reason FROM source_history WHERE source_key = %s ORDER BY id DESC LIMIT %s", (key, limit))

    def corroboration_stats(self, age_hours: int, days: int = 90) -> dict[str, tuple[int, int]]:
        rows = self.q(f"""WITH firsts AS (
                           SELECT DISTINCT ON (a.story_id) a.story_id, a.source_key
                           FROM articles a JOIN stories s ON s.id = a.story_id
                           WHERE a.seen_day >= current_date - %(days)s AND s.first_seen < now() - make_interval(hours => %(age)s)
                           ORDER BY a.story_id, a.published_at, a.id),
                         cnt AS (SELECT f.source_key,
                                   (SELECT count(DISTINCT a2.source_key) FROM {COVERAGE_ROWS} a2 WHERE a2.story_id = f.story_id AND a2.source_key <> f.source_key
                                      AND a2.seen_day >= current_date - %(days)s) AS others FROM firsts f)
                         SELECT source_key, count(*) AS n, count(*) FILTER (WHERE others >= 2) AS ok FROM cnt GROUP BY source_key""", {"days": days, "age": age_hours})
        return {r["source_key"]: (r["n"], r["ok"]) for r in rows}

    def recompute_coverage(self, days: int = 7) -> int:
        """Re-derive outlets and reliability-weighted coverage of recent stories after visibility or weights changed.
        Works in small batches in id order and skips rows the ingest is writing at that moment (a second pass picks them up),
        so it can never deadlock with ingestion."""
        calc = """WITH d AS (SELECT DISTINCT ON (story_id, source_key) story_id, source_key, COALESCE(fp, source_key) AS fp FROM (
                                 SELECT story_id, source_key, fp, published_at FROM articles WHERE seen_day >= current_date - %(days)s AND story_id IS NOT NULL
                                 UNION ALL SELECT story_id, source_key, fp, published_at FROM reprints WHERE seen_day >= current_date - %(days)s) u
                             WHERE TRUE {only} ORDER BY story_id, source_key, published_at),
                       g AS (SELECT d.story_id, d.fp, max(so.weight) FILTER (WHERE so.visible) AS w, count(*) FILTER (WHERE so.visible) AS k,
                                    (array_agg(so.country ORDER BY so.weight DESC) FILTER (WHERE so.visible AND so.type <> 'social'))[1] AS country
                             FROM d JOIN sources so ON so.key = d.source_key GROUP BY d.story_id, d.fp),
                       cc AS (SELECT story_id, country, sum(w) AS w FROM g WHERE country IS NOT NULL GROUP BY story_id, country),
                       f AS (SELECT story_id, sum(ln(1 + w) / ln(2)) AS fair FROM cc GROUP BY story_id)
                  SELECT g.story_id, COALESCE(sum(k), 0)::int AS outlets, (count(*) FILTER (WHERE k > 0))::int AS indep, COALESCE(sum(w), 0) AS cov,
                         COALESCE(max(f.fair), 0) AS fair
                  FROM g LEFT JOIN f ON f.story_id = g.story_id GROUP BY g.story_id"""
        total = 0
        for _pass in range(3):
            ids = [r["story_id"] for r in self.q(f"""SELECT c.story_id FROM ({calc.format(only='')}) c JOIN stories s ON s.id = c.story_id
                                                    WHERE s.outlets <> c.outlets OR s.indep <> c.indep OR abs(s.coverage - c.cov) > 0.0001
                                                    OR abs(s.fair - c.fair) > 0.0001 ORDER BY c.story_id""", {"days": days})]
            if not ids:
                break
            for i in range(0, len(ids), 300):
                total += self.run(f"""WITH locked AS (SELECT id FROM stories WHERE id = ANY(%(ids)s) ORDER BY id FOR UPDATE SKIP LOCKED),
                                      calc AS ({calc.format(only='AND story_id IN (SELECT id FROM locked)')})
                                      UPDATE stories s SET outlets = calc.outlets, indep = calc.indep, coverage = calc.cov, fair = calc.fair
                                      FROM calc WHERE s.id = calc.story_id""",
                                   {"ids": ids[i:i + 300], "days": days})
        return total
