-- Syndication-aware counting, and room for language and translation.
--   articles.fp      fingerprint of an article's text: outlets printing the same wire copy share it and count once
--                    (filled for new articles at ingest and for stored ones by the one-off retag job).
--   stories.indep    independent outlets (distinct fingerprints among visible sources); Top, Trending and Breaking use this.
--   articles.lang    language detected from the text; title_en / lead_en hold an English translation when there is one.
ALTER TABLE articles ADD COLUMN fp text, ADD COLUMN lang text, ADD COLUMN title_en text, ADD COLUMN lead_en text;
ALTER TABLE stories ADD COLUMN indep int NOT NULL DEFAULT 1, ADD COLUMN title_en text, ADD COLUMN lang text;
UPDATE stories SET indep = outlets;
CREATE INDEX articles_fp ON articles (fp);
CREATE INDEX stories_indep ON stories (indep) WHERE indep >= 2;
-- search the English translation too
ALTER TABLE stories DROP COLUMN tsv;
ALTER TABLE stories ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title_en, '') || ' ' || title || ' ' || lead)) STORED;
CREATE INDEX stories_tsv ON stories USING gin (tsv);
-- organic discovery: where each publisher was seen
ALTER TABLE web_hits ADD COLUMN editions text[] NOT NULL DEFAULT '{}', ADD COLUMN nation_editions text[] NOT NULL DEFAULT '{}',
    ADD COLUMN focus jsonb NOT NULL DEFAULT '{}', ADD COLUMN files int NOT NULL DEFAULT 0;
