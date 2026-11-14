-- Sources seen while searching the web (news search results): counted across runs, promoted to candidates once seen often enough.
CREATE TABLE web_hits (
    domain text PRIMARY KEY, name text NOT NULL DEFAULT '', hits int NOT NULL DEFAULT 0, queries int NOT NULL DEFAULT 0,
    first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(),
    country text NOT NULL DEFAULT '', language text NOT NULL DEFAULT '', homepage text, via text NOT NULL DEFAULT '', example text NOT NULL DEFAULT '',
    promoted boolean NOT NULL DEFAULT false
);
CREATE INDEX web_hits_ready ON web_hits (hits DESC) WHERE NOT promoted;
