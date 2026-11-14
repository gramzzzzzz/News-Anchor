-- Phase 2b to 4: source discovery and lifecycle, full-text search, story embeddings, article text cache, AI usage and cache.
CREATE EXTENSION IF NOT EXISTS vector;

-- Sources: where they came from and where they are in their life.
--   status: candidate (found, feed not yet validated) -> active -> degraded -> dormant -> retired; no_feed = no usable feed found yet
ALTER TABLE sources
    ADD COLUMN status text NOT NULL DEFAULT 'active',
    ADD COLUMN status_note text NOT NULL DEFAULT '',
    ADD COLUMN status_changed_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN language text NOT NULL DEFAULT 'en',
    ADD COLUMN origin text NOT NULL DEFAULT 'starter',            -- starter | owner | mediacloud | wikidata
    ADD COLUMN mc_id bigint,
    ADD COLUMN homepage text,
    ADD COLUMN media_type text,
    ADD COLUMN stories_per_week real,
    ADD COLUMN discovered_at timestamptz,
    ADD COLUMN feed_search_at timestamptz,
    ADD COLUMN feed_search_tries int NOT NULL DEFAULT 0,
    ADD COLUMN trust_note text NOT NULL DEFAULT '';
CREATE INDEX sources_status ON sources (status);
CREATE UNIQUE INDEX sources_mc_id ON sources (mc_id) WHERE mc_id IS NOT NULL;
CREATE INDEX sources_domain ON sources (domain);
ALTER TABLE feeds ADD COLUMN origin text NOT NULL DEFAULT 'starter', ADD COLUMN kind text NOT NULL DEFAULT 'rss';   -- rss | sitemap
ALTER TABLE feeds ADD COLUMN retired_at timestamptz;

-- Full-text search: english stemming on headline and lead. Partition indexes are created with each new day.
ALTER TABLE articles ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || lead)) STORED;
CREATE INDEX articles_tsv ON articles USING gin (tsv);
ALTER TABLE stories ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || lead)) STORED;
CREATE INDEX stories_tsv ON stories USING gin (tsv);

-- Story meaning vector (mean of its articles' headline vectors); exact search over recent stories is fast enough, so no vector index.
ALTER TABLE stories ADD COLUMN embedding vector(384), ADD COLUMN embed_n int NOT NULL DEFAULT 0;

-- Full text fetched on demand for the reading pane (not kept longer than the owner's retention).
CREATE TABLE article_text (
    url_hash text PRIMARY KEY, url text NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(),
    method text NOT NULL, title text NOT NULL DEFAULT '', text text NOT NULL DEFAULT '', words int NOT NULL DEFAULT 0,
    note text NOT NULL DEFAULT '', archive_url text
);

-- AI: one row per call (usage page, budgets) and a cache of finished outputs.
CREATE TABLE ai_usage (
    id bigserial PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), provider text NOT NULL, model text NOT NULL, task text NOT NULL,
    tokens_in int NOT NULL DEFAULT 0, tokens_out int NOT NULL DEFAULT 0, ms int NOT NULL DEFAULT 0, ok boolean NOT NULL, error text NOT NULL DEFAULT ''
);
CREATE INDEX ai_usage_at ON ai_usage (at DESC);
CREATE TABLE ai_cache (
    key text PRIMARY KEY, task text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), provider text NOT NULL, model text NOT NULL,
    prompt_version text NOT NULL, output jsonb NOT NULL
);
CREATE INDEX ai_cache_created ON ai_cache (created_at);
CREATE TABLE ai_provider_state (
    provider text PRIMARY KEY, blocked_until timestamptz, last_error text NOT NULL DEFAULT '', calls_today int NOT NULL DEFAULT 0, day date NOT NULL DEFAULT current_date
);
