-- The seen-state and new-development features of migration 018 are removed (they did not earn their place); chain_id (017) stays.
DROP TABLE IF EXISTS story_seen;
ALTER TABLE stories DROP COLUMN IF EXISTS developed_at;
ALTER TABLE stories DROP COLUMN IF EXISTS dev_note;
