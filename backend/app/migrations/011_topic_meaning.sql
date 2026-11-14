-- The meaning check: a story's sub-topic tag whose headline's meaning does not fit that sub-topic is kept but marked rejected,
-- and rejected tags are left out of lists, counts and the story's tags.
ALTER TABLE story_topics ADD COLUMN rejected boolean NOT NULL DEFAULT false;
