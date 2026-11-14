-- Google News links already opened (or found unnecessary), so a story is never looked up twice. Kept 10 days.
CREATE TABLE gnews_links (gid text PRIMARY KEY, url text, at timestamptz NOT NULL DEFAULT now());
CREATE INDEX gnews_links_at ON gnews_links (at);
-- Home pages read for new article links (sources with no feed and no news sitemap): the links seen, so only new ones become articles.
CREATE TABLE homepage_seen (feed_id integer NOT NULL REFERENCES feeds(id) ON DELETE CASCADE, url_hash text NOT NULL,
                            seen_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (feed_id, url_hash));
-- Sources whose feed search failed are searched again now, with the news-sitemap and home-page fallbacks.
UPDATE sources SET feed_search_at = now() - interval '400 days' WHERE status = 'no_feed';
-- Hacker News is read as a community site from its front page above 100 points; the old address would collect everything twice.
UPDATE feeds SET state = 'retired', retired_at = now() WHERE url = 'https://hnrss.org/frontpage';
