-- News Anchor schema, version 1. Applied once by the repository; later changes are new numbered files.
CREATE TABLE IF NOT EXISTS schema_version (version int PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE settings (key text PRIMARY KEY, value jsonb NOT NULL, changed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE kv (key text PRIMARY KEY, value jsonb NOT NULL);
CREATE TABLE audit (id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), kind text NOT NULL, detail text NOT NULL DEFAULT '');

CREATE TABLE sources (
    key text PRIMARY KEY, name text NOT NULL, domain text NOT NULL, country text NOT NULL,
    type text NOT NULL, scope text NOT NULL DEFAULT 'international',
    provisional boolean NOT NULL DEFAULT true, blocked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feeds (
    id serial PRIMARY KEY, source_key text NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    url text NOT NULL UNIQUE, topics text[] NOT NULL DEFAULT '{}', places text[] NOT NULL DEFAULT '{}',
    place_mode text NOT NULL DEFAULT 'inherit',            -- inherit | none | explicit
    etag text, last_modified text, poll_interval_s int NOT NULL DEFAULT 900,
    next_poll_at timestamptz NOT NULL DEFAULT now(), last_poll_at timestamptz, last_ok_at timestamptz, last_new_at timestamptz,
    fail_count int NOT NULL DEFAULT 0, state text NOT NULL DEFAULT 'active',        -- active | degraded | dormant | blocked
    last_status int, last_error text, items_total bigint NOT NULL DEFAULT 0
);
CREATE INDEX feeds_due ON feeds (next_poll_at) WHERE state <> 'blocked';

CREATE TABLE topic_prefs (code text PRIMARY KEY, enabled boolean NOT NULL);
CREATE TABLE favourites (
    id serial PRIMARY KEY, place text NOT NULL DEFAULT 'world', topic text NOT NULL DEFAULT '',
    position int NOT NULL DEFAULT 0, UNIQUE (place, topic)
);

CREATE TABLE stories (
    id bigserial PRIMARY KEY, title text NOT NULL, lead text NOT NULL DEFAULT '',
    first_seen timestamptz NOT NULL, last_update timestamptz NOT NULL,
    outlets int NOT NULL DEFAULT 1, articles int NOT NULL DEFAULT 1, tokens text[] NOT NULL DEFAULT '{}',
    lead_source text NOT NULL, lead_url text NOT NULL, breaking_until timestamptz,
    countries text[] NOT NULL DEFAULT '{}', types text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX stories_last_update ON stories (last_update DESC);
CREATE INDEX stories_tokens ON stories USING gin (tokens);
CREATE TABLE story_topics (story_id bigint NOT NULL REFERENCES stories(id) ON DELETE CASCADE, code text NOT NULL, n int NOT NULL, PRIMARY KEY (story_id, code));
CREATE INDEX story_topics_code ON story_topics (code, story_id);
CREATE TABLE story_places (story_id bigint NOT NULL REFERENCES stories(id) ON DELETE CASCADE, code text NOT NULL, n int NOT NULL, PRIMARY KEY (story_id, code));
CREATE INDEX story_places_code ON story_places (code, story_id);
CREATE TABLE saved (story_id bigint PRIMARY KEY REFERENCES stories(id) ON DELETE CASCADE, saved_at timestamptz NOT NULL DEFAULT now());

-- One partition per day (by first-seen day): dropping a day frees its disk at once.
CREATE TABLE articles (
    id bigint GENERATED ALWAYS AS IDENTITY, seen_day date NOT NULL,
    source_key text NOT NULL, feed_id int, url text NOT NULL, url_hash text NOT NULL,
    title text NOT NULL, lead text NOT NULL DEFAULT '', published_at timestamptz NOT NULL, seen_at timestamptz NOT NULL,
    story_id bigint, topics text[] NOT NULL DEFAULT '{}', places text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, seen_day)
) PARTITION BY RANGE (seen_day);
CREATE INDEX articles_url_hash ON articles (url_hash);
CREATE INDEX articles_story ON articles (story_id);
CREATE INDEX articles_seen ON articles (seen_at);

CREATE TABLE jobs (
    id bigserial PRIMARY KEY, kind text NOT NULL, state text NOT NULL DEFAULT 'queued',
    progress jsonb NOT NULL DEFAULT '{}', result jsonb, created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz, finished_at timestamptz
);
