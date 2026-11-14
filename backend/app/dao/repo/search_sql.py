"""SQL for embeddings, story merging, search and the Explore statistics (mixed into Repository)."""
from __future__ import annotations

import datetime as dt
from typing import Any

UTC = dt.timezone.utc


def vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


class SearchSQL:
    # ---- embeddings and merging ------------------------------------------------------------------
    def stories_to_embed(self, days: int, limit: int, backend: str) -> list[dict]:
        """Recent English stories (or ones with an English translation) with no vector, or one made by another method."""
        return self.q("""SELECT id, COALESCE(title_en, title) AS text, COALESCE(lead, '') AS lead FROM stories
                         WHERE (embedding IS NULL OR embed_backend IS DISTINCT FROM %s) AND (COALESCE(lang, 'en') = 'en' OR title_en IS NOT NULL)
                         AND last_update > now() - make_interval(days => %s) ORDER BY last_update DESC LIMIT %s""", (backend, days, limit))

    def window_rows(self, hours: int, backend: str):
        """The clustering window, streamed (a server-side cursor, 2,000 rows at a time) so the vectors' text is never all in memory at once."""
        with self.pool.connection() as c, c.cursor(name="clustering_window") as cur:
            cur.itersize = 2000
            cur.execute("""SELECT s.id, COALESCE(s.title_en, s.title) AS title, s.tokens, s.first_seen, s.last_update, s.embedding::text AS e, s.lead_source, s.outlets,
                                  COALESCE(s.thread_id, s.id) AS thread_id,
                                  ARRAY(SELECT code FROM story_places p WHERE p.story_id = s.id AND p.n * 2 >= s.articles) AS places
                           FROM stories s WHERE s.embedding IS NOT NULL AND s.embed_backend = %s AND (COALESCE(s.lang, 'en') = 'en' OR s.title_en IS NOT NULL)
                           AND s.last_update > now() - make_interval(hours => %s)""", (backend, hours))
            yield from cur

    def link_threads(self, pairs: list[tuple[int, int]]) -> int:
        """Join storylines: for each (a, b) pair of developments, every development of b's thread moves to a's thread."""
        n = 0
        with self.pool.connection() as c:
            for a, b in pairs:
                n += c.execute("""UPDATE stories SET thread_id = (SELECT COALESCE(thread_id, id) FROM stories WHERE id = %(a)s)
                                  WHERE COALESCE(thread_id, id) = (SELECT COALESCE(thread_id, id) FROM stories WHERE id = %(b)s)
                                  AND COALESCE(thread_id, id) <> (SELECT COALESCE(thread_id, id) FROM stories WHERE id = %(a)s)""", {"a": a, "b": b}).rowcount
        return n

    # ---- storyline coherence ---------------------------------------------------------------------
    def thread_info(self, thread_ids: list[int]) -> dict[int, tuple[list[float], int]]:
        """{storyline: (mean vector of its developments, number of developments)} for the storylines that have vectors."""
        if not thread_ids:
            return {}
        out = {}
        for r in self.q("""SELECT thread_id AS t, count(*) AS n, avg(embedding)::text AS e FROM stories
                           WHERE thread_id = ANY(%s) AND embedding IS NOT NULL GROUP BY thread_id""", (thread_ids,)):
            if r["e"]:
                out[r["t"]] = ([float(x) for x in r["e"].strip("[]").split(",")], int(r["n"]))
        return out

    def thread_count(self, thread_id: int) -> tuple[int, dt.datetime | None]:
        r = self.one("SELECT count(*) AS n, min(first_seen) AS since FROM stories WHERE COALESCE(thread_id, id) = %s", (thread_id,))
        return int(r["n"]), r["since"]

    def thread_members(self, thread_id: int) -> list[dict]:
        """Developments of a storyline with their meaning vectors (as text) and headline words."""
        return self.q("""SELECT id, COALESCE(title_en, title) AS title, tokens, first_seen, embedding::text AS e FROM stories
                         WHERE thread_id = %s AND embedding IS NOT NULL ORDER BY first_seen, id""", (thread_id,))

    def thread_ids_all(self, thread_id: int) -> list[int]:
        return [r["id"] for r in self.q("SELECT id FROM stories WHERE thread_id = %s", (thread_id,))]

    def threads_to_check(self, min_size: int, active_hours: int | None = None) -> list[int]:
        """Storylines of at least ``min_size`` developments (with, if given, one updated in the last ``active_hours``), largest first."""
        having = "count(*) >= %(m)s" + (" AND max(last_update) > now() - make_interval(hours => %(h)s)" if active_hours else "")
        return [r["thread_id"] for r in self.q(f"SELECT thread_id FROM stories WHERE thread_id IS NOT NULL GROUP BY thread_id HAVING {having} ORDER BY count(*) DESC",
                                              {"m": min_size, "h": active_hours})]

    def thread_coherence(self, thread_ids: list[int]) -> dict[int, tuple[int, float]]:
        """{storyline: (developments, mean similarity of its developments to the storyline's centre)}."""
        if not thread_ids:
            return {}
        rows = self.q("""WITH c AS (SELECT thread_id, avg(embedding) AS cen FROM stories WHERE thread_id = ANY(%(t)s) AND embedding IS NOT NULL GROUP BY thread_id)
                         SELECT s.thread_id AS t, count(*) AS n, avg(1 - (s.embedding <=> c.cen)) AS coh FROM stories s JOIN c ON c.thread_id = s.thread_id
                         WHERE s.embedding IS NOT NULL GROUP BY s.thread_id""", {"t": thread_ids})
        return {r["t"]: (int(r["n"]), float(r["coh"])) for r in rows}

    def threads_split_apply(self, old: int, groups: list[list[int]], loose: list[int]) -> int:
        """Replace storyline ``old`` by ``groups`` (each a storyline named by its earliest development) and make each of ``loose`` its own.
        Skips a group whose name is already another storyline's. Returns the number of storylines made."""
        made = 0
        with self.pool.connection() as c:
            taken = {r["thread_id"] for r in c.execute("SELECT DISTINCT thread_id FROM stories WHERE thread_id = ANY(%s) AND thread_id <> %s",
                                                       ([min(g) for g in groups if g], old)).fetchall()}
            names = []
            for g in groups:
                if not g or min(g) in taken:
                    continue
                c.execute("UPDATE stories SET thread_id = %s WHERE id = ANY(%s)", (min(g), g))
                names.append(min(g))
                made += 1
            if loose:
                c.execute("UPDATE stories SET thread_id = id WHERE id = ANY(%s)", (loose,))
            c.execute("DELETE FROM storyline_titles WHERE thread_id = ANY(%s)", ([old] + names,))
        return made

    def window_for_clustering(self, hours: int, backend: str) -> list[dict]:
        return self.q("""SELECT s.id, COALESCE(s.title_en, s.title) AS title, s.tokens, s.first_seen, s.last_update, s.embedding::text AS e,
                                ARRAY(SELECT code FROM story_places p WHERE p.story_id = s.id AND p.n * 2 >= s.articles) AS places
                         FROM stories s WHERE s.embedding IS NOT NULL AND s.embed_backend = %s AND (COALESCE(s.lang, 'en') = 'en' OR s.title_en IS NOT NULL)
                         AND s.last_update > now() - make_interval(hours => %s)""", (backend, hours))

    def story_embeddings_set(self, rows: list[tuple[int, list[float]]], backend: str) -> None:
        with self.pool.connection() as c, c.cursor() as cur:
            cur.executemany("UPDATE stories SET embedding = %s::vector, embed_n = 1, embed_backend = %s WHERE id = %s", [(vec(v), backend, i) for i, v in rows])

    def topic_rejections_set(self, ids: list[int], want: list[tuple[int, str, bool]]) -> int:
        """The meaning check's verdicts for these stories: tags in ``want`` marked True are rejected, every other tag of them is not."""
        bad = [(s, c) for s, c, r in want if r]
        with self.pool.connection() as c, c.cursor() as cur:
            cur.execute("""UPDATE story_topics t SET rejected = false WHERE t.story_id = ANY(%s) AND t.rejected
                           AND NOT EXISTS (SELECT 1 FROM unnest(%s::bigint[], %s::text[]) AS b(sid, code) WHERE b.sid = t.story_id AND b.code = t.code)""",
                        (ids, [x for x, _ in bad], [y for _, y in bad]))
            n = cur.rowcount
            cur.execute("""UPDATE story_topics t SET rejected = true FROM unnest(%s::bigint[], %s::text[]) AS b(sid, code)
                           WHERE t.story_id = b.sid AND t.code = b.code AND NOT t.rejected""", ([x for x, _ in bad], [y for _, y in bad]))
            return n + cur.rowcount

    def embedded_count(self, backend: str | None = None) -> dict:
        return self.one("""SELECT count(*) FILTER (WHERE embedding IS NOT NULL AND (%s::text IS NULL OR embed_backend = %s)) AS embedded, count(*) AS stories
                           FROM stories""", (backend, backend))

    def story_neighbours(self, story_id: int, hours: int, limit: int = 5) -> list[dict]:
        """Nearest other stories updated within ``hours`` (cosine similarity), for merging."""
        return self.q("""SELECT o.id, o.title, o.tokens, o.first_seen, 1 - (o.embedding <=> m.embedding) AS sim
                         FROM stories m JOIN stories o ON o.id <> m.id AND o.embedding IS NOT NULL AND o.embed_backend = m.embed_backend AND o.last_update > now() - make_interval(hours => %s)
                         WHERE m.id = %s AND m.embedding IS NOT NULL ORDER BY o.embedding <=> m.embedding LIMIT %s""", (hours, story_id, limit))

    def story_brief(self, story_id: int) -> dict | None:
        return self.one("SELECT id, title, tokens, first_seen FROM stories WHERE id = %s", (story_id,))

    def merge_stories(self, keep: int, drop: int, floor_days: int = 6, carry_thread: bool = True) -> int | None:
        """Fold story ``drop`` into ``keep`` and recompute ``keep``. Returns its outlet count (None if either story is gone)."""
        with self.pool.connection() as c:
            rows = {r["id"]: r for r in c.execute("SELECT id, embedding::text AS e, embed_n, embed_backend AS b FROM stories WHERE id = ANY(%s) ORDER BY id FOR UPDATE", ([keep, drop],)).fetchall()}
            if len(rows) < 2:
                return None
            mean = None
            ek, ed = rows[keep], rows[drop]
            if ek["e"] and ed["e"] and ek["b"] == ed["b"]:
                a, b = [float(x) for x in ek["e"].strip("[]").split(",")], [float(x) for x in ed["e"].strip("[]").split(",")]
                na, nb = max(ek["embed_n"], 1), max(ed["embed_n"], 1)
                m = [(x * na + y * nb) / (na + nb) for x, y in zip(a, b)]
                norm = sum(x * x for x in m) ** 0.5 or 1.0
                mean = vec([x / norm for x in m])
            elif ed["e"] and not ek["e"]:
                mean = ed["e"]
            if mean and ed["e"] and not ek["e"]:
                c.execute("UPDATE stories SET embed_backend = %s WHERE id = %s", (ed["b"], keep))
            c.execute("UPDATE articles SET story_id = %s WHERE story_id = %s", (keep, drop))
            c.execute("UPDATE reprints SET story_id = %s WHERE story_id = %s", (keep, drop))
            if carry_thread:
                # the dropped development's storyline joins the kept one's (the merger checks first that the two storylines agree)
                c.execute("""UPDATE stories SET thread_id = (SELECT COALESCE(thread_id, id) FROM stories WHERE id = %(k)s)
                             WHERE thread_id = (SELECT COALESCE(thread_id, id) FROM stories WHERE id = %(d)s) AND id <> %(d)s""", {"k": keep, "d": drop})
            for t in ("story_topics", "story_places"):
                c.execute(f"""INSERT INTO {t} (story_id, code, n) SELECT %s, code, n FROM {t} WHERE story_id = %s
                              ON CONFLICT (story_id, code) DO UPDATE SET n = {t}.n + EXCLUDED.n""", (keep, drop))
            c.execute("UPDATE saved SET story_id = %s WHERE story_id = %s AND NOT EXISTS (SELECT 1 FROM saved WHERE story_id = %s)", (keep, drop, keep))
            c.execute("""UPDATE stories k SET
                           first_seen = LEAST(k.first_seen, d.first_seen), last_update = GREATEST(k.last_update, d.last_update),
                           articles = (SELECT count(*) FROM articles WHERE story_id = %(keep)s),
                           tokens = (SELECT array(SELECT DISTINCT unnest(k.tokens || d.tokens))),
                           countries = (SELECT array(SELECT DISTINCT unnest(k.countries || d.countries))),
                           types = (SELECT array(SELECT DISTINCT unnest(k.types || d.types))),
                           breaking_until = GREATEST(k.breaking_until, d.breaking_until),
                           embedding = COALESCE(%(mean)s::vector, k.embedding), embed_n = k.embed_n + d.embed_n
                         FROM stories d WHERE k.id = %(keep)s AND d.id = %(drop)s""", {"keep": keep, "drop": drop, "mean": mean})
            c.execute("DELETE FROM stories WHERE id = %s", (drop,))
            from app.dao.repo.postgres import STORY_COUNTS, STORY_COUNTS_SET
            return c.execute(STORY_COUNTS + " UPDATE stories SET " + STORY_COUNTS_SET + " WHERE id = %(sid)s RETURNING indep",
                             {"sid": keep, "floor": dt.date.today() - dt.timedelta(days=floor_days)}).fetchone()["indep"]

    # ---- search ----------------------------------------------------------------------------------
    @staticmethod
    def _story_filters(p: dict, *, place: str, topic: str, since: dt.datetime | None, until: dt.datetime | None) -> list[str]:
        w = []
        if since:
            w.append("s.last_update >= %(since)s"); p["since"] = since
        if until:
            w.append("s.first_seen <= %(until)s"); p["until"] = until
        if place and place != "world":
            w.append("EXISTS (SELECT 1 FROM story_places x WHERE x.story_id = s.id AND x.code = ANY(%(place)s) AND x.n * 2 >= s.articles)")
            p["place"] = list(place) if isinstance(place, (list, tuple)) else [place]
        if topic:
            w.append("EXISTS (SELECT 1 FROM story_topics y WHERE y.story_id = s.id AND y.code = ANY(%(topic)s) AND y.n * 3 >= s.articles AND NOT y.rejected)")
            p["topic"] = list(topic) if isinstance(topic, (list, tuple)) else [topic]
        return w

    def search_lexical(self, query: str, *, place: str = "", topic: str = "", since: dt.datetime | None = None, until: dt.datetime | None = None, limit: int = 50) -> list[dict]:
        p: dict[str, Any] = {"q": query, "lim": limit}
        w = ["s.tsv @@ websearch_to_tsquery('english', %(q)s)"] + self._story_filters(p, place=place, topic=topic, since=since, until=until)
        return self.q(f"""SELECT s.id, ts_rank_cd(s.tsv, websearch_to_tsquery('english', %(q)s)) AS rank,
                          ts_headline('english', s.title, websearch_to_tsquery('english', %(q)s), 'StartSel=«, StopSel=», MaxFragments=1, MaxWords=30') AS hit
                          FROM stories s WHERE {' AND '.join(w)} ORDER BY rank DESC, s.last_update DESC LIMIT %(lim)s""", p)

    def search_semantic(self, qvec: list[float], backend: str, *, place: str = "", topic: str = "", since: dt.datetime | None = None, until: dt.datetime | None = None, limit: int = 50) -> list[dict]:
        p: dict[str, Any] = {"v": vec(qvec), "lim": limit, "be": backend}
        w = ["s.embedding IS NOT NULL", "s.embed_backend = %(be)s"] + self._story_filters(p, place=place, topic=topic, since=since, until=until)
        return self.q(f"""SELECT s.id, 1 - (s.embedding <=> %(v)s::vector) AS sim FROM stories s WHERE {' AND '.join(w)}
                          ORDER BY s.embedding <=> %(v)s::vector LIMIT %(lim)s""", p)

    def stories_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        return self.q("""SELECT s.id, s.title, s.lead, s.first_seen, s.last_update, s.outlets, s.indep, s.articles, s.lead_source, s.lead_url, s.title_en, s.lang,
                         (s.breaking_until > now()) AS breaking, EXISTS (SELECT 1 FROM saved v WHERE v.story_id = s.id) AS is_saved
                         FROM stories s WHERE s.id = ANY(%s)""", (ids,))

    # ---- explore ---------------------------------------------------------------------------------
    @staticmethod
    def _article_filters(p: dict, f: dict) -> list[str]:
        w = ["a.seen_day >= %(day0)s", "a.published_at >= %(since)s", "a.published_at < %(until)s"]
        p.update(day0=(f["since"] - dt.timedelta(days=3)).date(), since=f["since"], until=f["until"])
        if f.get("q"):
            w.append("a.tsv @@ websearch_to_tsquery('english', %(q)s)"); p["q"] = f["q"]
        if f.get("topic"):
            w.append("%(topic)s = ANY(a.topics)"); p["topic"] = f["topic"]
        if f.get("place") and f["place"] != "world":
            pl = f["place"] if isinstance(f["place"], (list, tuple)) else [f["place"]]
            w.append("a.places && %(place)s::text[]"); p["place"] = list(pl)
        if f.get("types"):
            w.append("s.type = ANY(%(types)s)"); p["types"] = f["types"]
        if f.get("bands"):
            w.append("s.band = ANY(%(bands)s)"); p["bands"] = f["bands"]
        if f.get("countries"):
            w.append("s.country = ANY(%(countries)s)"); p["countries"] = f["countries"]
        if f.get("sources"):
            w.append("a.source_key = ANY(%(sources)s)"); p["sources"] = f["sources"]
        return w

    def explore(self, f: dict, bucket: str, sample_limit: int, sample_offset: int) -> dict:
        p: dict[str, Any] = {"bucket": bucket}
        w = " AND ".join(self._article_filters(p, f))
        base = f"FROM articles a JOIN sources s ON s.key = a.source_key WHERE {w}"
        out: dict[str, Any] = {}
        out["totals"] = self.one(f"SELECT count(*) AS articles, count(DISTINCT a.story_id) AS stories, count(DISTINCT a.source_key) AS sources, count(DISTINCT s.country) AS countries {base}", p)
        out["timeline"] = self.q(f"""SELECT date_trunc(%(bucket)s, a.published_at AT TIME ZONE 'UTC') AS t, count(*) AS n,
                                     count(*) FILTER (WHERE s.band IN ('highly_reliable', 'reliable')) AS reliable,
                                     count(*) FILTER (WHERE s.band IN ('mixed', 'low', 'blocked')) AS weak,
                                     count(*) FILTER (WHERE s.band = 'unrated') AS unrated {base} GROUP BY 1 ORDER BY 1""", p)
        out["sources"] = self.q(f"SELECT a.source_key AS key, s.name, s.band, s.type, s.country, count(*) AS n {base} GROUP BY 1, 2, 3, 4, 5 ORDER BY n DESC LIMIT 25", p)
        out["countries"] = self.q(f"SELECT s.country AS code, count(*) AS n, count(DISTINCT a.source_key) AS sources {base} GROUP BY 1 ORDER BY n DESC LIMIT 25", p)
        out["bands"] = self.q(f"SELECT s.band, count(*) AS n {base} GROUP BY 1 ORDER BY n DESC", p)
        out["types"] = self.q(f"SELECT s.type, count(*) AS n {base} GROUP BY 1 ORDER BY n DESC", p)
        out["topics"] = self.q(f"SELECT t AS code, count(*) AS n {base.replace('WHERE', 'CROSS JOIN LATERAL unnest(a.topics) AS t WHERE', 1)} GROUP BY 1 ORDER BY n DESC LIMIT 40", p)
        out["places"] = self.q(f"SELECT t AS code, count(*) AS n {base.replace('WHERE', 'CROSS JOIN LATERAL unnest(a.places) AS t WHERE', 1)} GROUP BY 1 ORDER BY n DESC LIMIT 40", p)
        out["titles"] = [r["title"] for r in self.q(f"SELECT a.title {base} ORDER BY a.published_at DESC LIMIT 4000", p)]
        out["stories_top"] = self.q(f"""SELECT a.story_id AS id, count(*) AS articles, count(DISTINCT a.source_key) AS outlets, max(a.title) AS title, max(a.published_at) AS latest
                                        {base} AND a.story_id IS NOT NULL GROUP BY 1 ORDER BY outlets DESC, articles DESC LIMIT 12""", p)
        p2 = {**p, "lim": sample_limit, "off": sample_offset}
        out["articles"] = self.q(f"""SELECT a.id, a.title, a.url, a.published_at, a.story_id, s.key AS source_key, s.name AS source_name, s.band, s.type, s.country
                                     {base} ORDER BY a.published_at DESC LIMIT %(lim)s OFFSET %(off)s""", p2)
        return out

    def explore_timeline_only(self, f: dict, bucket: str) -> list[dict]:
        p: dict[str, Any] = {"bucket": bucket}
        w = " AND ".join(self._article_filters(p, f))
        return self.q(f"""SELECT date_trunc(%(bucket)s, a.published_at AT TIME ZONE 'UTC') AS t, count(*) AS n
                          FROM articles a JOIN sources s ON s.key = a.source_key WHERE {w} GROUP BY 1 ORDER BY 1""", p)

    # ---- translation -------------------------------------------------------------------------------
    def untranslated_by_language(self, days: int) -> list[tuple[str, int]]:
        return [(r["lang"], r["n"]) for r in self.q("""SELECT lang, count(*) AS n FROM articles WHERE seen_day >= current_date - %s AND lang IS NOT NULL
                                                       AND lang <> 'en' AND title_en IS NULL GROUP BY 1 ORDER BY 2 DESC""", (days,))]

    def untranslated_articles(self, lang: str, limit: int, days: int) -> list[dict]:
        return self.q("""SELECT id, seen_day, title, lead FROM articles WHERE seen_day >= current_date - %s AND lang = %s AND title_en IS NULL
                         ORDER BY seen_at DESC LIMIT %s""", (days, lang, limit))

    def translations_set(self, rows: list[tuple]) -> None:
        """rows: (title_en, lead_en, id, seen_day). A story takes the translation of the article its headline came from."""
        with self.pool.connection() as c:
            c.cursor().executemany("UPDATE articles SET title_en = %s, lead_en = %s WHERE id = %s AND seen_day = %s", rows)
            c.execute("""UPDATE stories s SET title_en = a.title_en FROM articles a
                         WHERE a.id = ANY(%s) AND a.story_id = s.id AND a.title = s.title AND s.title_en IS NULL""", ([r[2] for r in rows],))

    # ---- article text cache and AI -----------------------------------------------------------------
    def text_get(self, url_hash: str) -> dict | None:
        """The stored text of an article. A failed read is kept for 6 hours only, then tried again (sites unblock, pages change)."""
        return self.one("""SELECT * FROM article_text WHERE url_hash = %s
                           AND (method <> 'summary only' OR fetched_at > now() - interval '6 hours')""", (url_hash,))

    def text_put(self, r: dict) -> None:
        self.run("""INSERT INTO article_text (url_hash, url, method, title, text, words, note, archive_url) VALUES (%(url_hash)s, %(url)s, %(method)s, %(title)s, %(text)s, %(words)s, %(note)s, %(archive_url)s)
                    ON CONFLICT (url_hash) DO UPDATE SET fetched_at = now(), method = EXCLUDED.method, title = EXCLUDED.title, text = EXCLUDED.text,
                    words = EXCLUDED.words, note = EXCLUDED.note, archive_url = EXCLUDED.archive_url""", r)

    def article_get(self, aid: int) -> dict | None:
        return self.one("""SELECT a.id, a.url, a.url_hash, a.title, a.lead, a.published_at, a.story_id, s.key AS source_key, s.name AS source_name, s.type AS source_type,
                           s.country AS source_country, s.band AS source_band, s.owner AS source_owner, s.domain AS source_domain
                           FROM articles a JOIN sources s ON s.key = a.source_key WHERE a.id = %s ORDER BY a.seen_day DESC LIMIT 1""", (aid,))

    def ai_cache_get(self, key: str, max_age_h: int) -> dict | None:
        return self.one("SELECT * FROM ai_cache WHERE key = %s AND created_at > now() - make_interval(hours => %s)", (key, max_age_h))

    def ai_cache_put(self, key: str, task: str, provider: str, model: str, version: str, output: dict) -> None:
        from psycopg.types.json import Jsonb
        self.run("""INSERT INTO ai_cache (key, task, provider, model, prompt_version, output) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET created_at = now(), provider = EXCLUDED.provider, model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version, output = EXCLUDED.output""",
                 (key, task, provider, model, version, Jsonb(output)))
        self.run("DELETE FROM ai_cache WHERE created_at < now() - interval '30 days'")

    def ai_cache_latest(self, prefix: str, max_age_h: int) -> dict | None:
        """The newest finished reading whose key starts with ``prefix`` (a story or scope), whatever material it was made from."""
        return self.one("SELECT * FROM ai_cache WHERE key LIKE %s AND created_at > now() - make_interval(hours => %s) ORDER BY created_at DESC LIMIT 1",
                        (prefix.replace("%", "") + "%", max_age_h))

    def ai_usage_add(self, provider: str, model: str, task: str, tin: int, tout: int, ms: int, ok: bool, error: str) -> None:
        self.run("INSERT INTO ai_usage (provider, model, task, tokens_in, tokens_out, ms, ok, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                 (provider, model, task, tin, tout, ms, ok, error[:300]))
        self.run("DELETE FROM ai_usage WHERE at < now() - interval '90 days'")

    def ai_usage_summary(self) -> dict:
        today = self.q("""SELECT provider, count(*) AS calls, count(*) FILTER (WHERE ok) AS ok, COALESCE(sum(tokens_in), 0) AS tin, COALESCE(sum(tokens_out), 0) AS tout
                          FROM ai_usage WHERE at >= date_trunc('day', now()) GROUP BY 1 ORDER BY 2 DESC""")
        week = self.q("""SELECT task, count(*) AS calls FROM ai_usage WHERE at > now() - interval '7 days' GROUP BY 1 ORDER BY 2 DESC""")
        recent = self.q("SELECT at, provider, model, task, tokens_in, tokens_out, ms, ok, error FROM ai_usage ORDER BY id DESC LIMIT 25")
        total_today = self.one("SELECT count(*) AS n FROM ai_usage WHERE at >= date_trunc('day', now())")["n"]
        return {"today": today, "week": week, "recent": recent, "calls_today": total_today}

    def ai_block(self, provider: str, until: dt.datetime | None, error: str) -> None:
        self.run("""INSERT INTO ai_provider_state (provider, blocked_until, last_error) VALUES (%s, %s, %s)
                    ON CONFLICT (provider) DO UPDATE SET blocked_until = EXCLUDED.blocked_until, last_error = EXCLUDED.last_error""", (provider, until, error[:300]))

    def ai_states(self) -> dict[str, dict]:
        return {r["provider"]: r for r in self.q("SELECT provider, blocked_until, last_error FROM ai_provider_state")}
