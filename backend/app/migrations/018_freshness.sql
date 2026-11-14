-- Freshness: the last time a story had a new development (new facts reported by several outlets), what was new, and what the reader has seen.
ALTER TABLE stories ADD COLUMN IF NOT EXISTS developed_at timestamptz;
ALTER TABLE stories ADD COLUMN IF NOT EXISTS dev_note text;
CREATE TABLE IF NOT EXISTS story_seen (
    story_id bigint PRIMARY KEY,
    seen_at timestamptz NOT NULL,
    kind text NOT NULL DEFAULT 'view',          -- 'view': it was on screen; 'open': it was opened
    outlets integer NOT NULL DEFAULT 0          -- independent outlets it had when seen (coverage added since brings it back)
);
