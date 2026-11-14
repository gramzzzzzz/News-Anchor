-- Top ranks the stories with the most coverage inside a period; the candidates for that are read by total coverage.
CREATE INDEX IF NOT EXISTS stories_fair ON stories (fair DESC) WHERE NOT routine;
