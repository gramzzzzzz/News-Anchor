-- v0.9.5: a storyline's own title, chosen from all its reports (services/headlines.py)
CREATE TABLE IF NOT EXISTS storyline_titles (
    thread_id  bigint PRIMARY KEY,
    title      text NOT NULL,
    lead       text NOT NULL DEFAULT '',
    url        text,
    source_key text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
