-- "Scheduled" (a future event date guessed from the wording of headlines) is removed: it was not reliable enough to show.
ALTER TABLE stories DROP COLUMN status, DROP COLUMN event_at;
