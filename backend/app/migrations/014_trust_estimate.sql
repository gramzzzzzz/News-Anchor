-- v0.9.4: who cites whom (trust evidence for sources without an expert rating), and whether a band is expert-rated or estimated
CREATE TABLE IF NOT EXISTS source_links (
    from_key  text NOT NULL,
    to_domain text NOT NULL,
    kind      text NOT NULL,          -- link (in a feed item) | page (in an article page) | mention (named as a source)
    n         integer NOT NULL DEFAULT 1,
    first_at  timestamptz NOT NULL DEFAULT now(),
    last_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_key, to_domain, kind)
);
CREATE INDEX IF NOT EXISTS source_links_to ON source_links (to_domain);
ALTER TABLE sources ADD COLUMN IF NOT EXISTS trust_basis text;     -- expert | estimated | NULL (unrated)
UPDATE sources SET trust_basis = CASE WHEN override_band IS NOT NULL THEN 'owner' WHEN band <> 'unrated' THEN 'expert' END WHERE trust_basis IS NULL;
