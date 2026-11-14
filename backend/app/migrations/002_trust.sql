-- Trust: evidence per source, reference datasets, the resulting score and band, and the visibility the owner's floor implies.
ALTER TABLE sources
    ADD COLUMN curated boolean NOT NULL DEFAULT true,           -- hand-picked or added by the owner (discovered candidates are false)
    ADD COLUMN score real, ADD COLUMN confidence real,
    ADD COLUMN band text NOT NULL DEFAULT 'unrated',           -- highly_reliable | reliable | mixed | low | unrated | blocked
    ADD COLUMN visible boolean NOT NULL DEFAULT true,          -- passes the owner's reliability floor
    ADD COLUMN weight real NOT NULL DEFAULT 0.46,              -- score^gamma, used in ranking
    ADD COLUMN owner text, ADD COLUMN wikidata_id text,
    ADD COLUMN override_band text,                             -- owner's final say
    ADD COLUMN block_reason text,
    ADD COLUMN trust_updated_at timestamptz;

CREATE TABLE trust_signals (
    source_key text NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    signal text NOT NULL, value real NOT NULL, factor real NOT NULL DEFAULT 1, weight real NOT NULL,
    detail text NOT NULL DEFAULT '', evidence_url text, evidence_date date,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, signal)
);
CREATE TABLE ref_scores (
    dataset text NOT NULL, domain text NOT NULL, value real NOT NULL, label text NOT NULL DEFAULT '',
    detail text NOT NULL DEFAULT '', url text, fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset, domain)
);
CREATE TABLE source_history (
    id bigserial PRIMARY KEY, source_key text NOT NULL, at timestamptz NOT NULL DEFAULT now(), change text NOT NULL, reason text NOT NULL DEFAULT ''
);
CREATE INDEX source_history_key ON source_history (source_key, id DESC);
ALTER TABLE stories ADD COLUMN coverage real NOT NULL DEFAULT 0.46;
UPDATE stories SET coverage = outlets * 0.46;
