-- v0.9.4: reprints are not stored as articles; storyline repair bookkeeping
CREATE TABLE IF NOT EXISTS reprints (
    url_hash     text PRIMARY KEY,
    story_id     bigint NOT NULL,
    source_key   text NOT NULL,
    fp           text,
    url          text NOT NULL,
    published_at timestamptz NOT NULL,
    seen_day     date NOT NULL
);
CREATE INDEX IF NOT EXISTS reprints_story ON reprints (story_id);
CREATE INDEX IF NOT EXISTS reprints_seen ON reprints (seen_day);
