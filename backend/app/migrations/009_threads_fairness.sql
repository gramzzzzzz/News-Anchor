-- Developments and threads. A story row is now one development: the reports of one event within a short window after its first
-- report. Developments of the same storyline share a thread_id (the id of the thread's first development).
--   status    reported | scheduled (the reports point to a future date, event_at)
--   fair      country-balanced coverage: per country, log2(1 + reliability-weighted independent outlets), summed over countries, so a
--             story carried in thirty countries outranks one carried by many outlets of a single country.
ALTER TABLE stories ADD COLUMN thread_id bigint, ADD COLUMN status text NOT NULL DEFAULT 'reported', ADD COLUMN event_at timestamptz,
                    ADD COLUMN fair real NOT NULL DEFAULT 0;
UPDATE stories SET thread_id = id;
CREATE INDEX stories_thread ON stories (thread_id);
CREATE INDEX stories_first_seen ON stories (first_seen DESC);
