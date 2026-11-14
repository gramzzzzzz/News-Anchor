-- A story's first and latest times now come from when its articles were published, not from when this server first fetched them:
-- a newly added source's backlog (items up to a day old) no longer looks new in Latest, Trending and the Top recency decay.
WITH t AS (SELECT story_id, min(LEAST(published_at, seen_at)) AS f, max(LEAST(published_at, seen_at)) AS l
           FROM articles WHERE seen_day >= current_date - 4 AND story_id IS NOT NULL GROUP BY story_id)
UPDATE stories s SET first_seen = t.f, last_update = t.l FROM t WHERE s.id = t.story_id AND (s.first_seen <> t.f OR s.last_update <> t.l);
-- Breaking marks were counted per article, not per outlet; they are set again as new reports arrive.
UPDATE stories SET breaking_until = NULL WHERE breaking_until > now();
