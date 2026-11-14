-- Routine items (scoreboards, schedules, bulletins, obituaries, horoscopes, dated photo galleries) are shown in Latest and in their
-- place and topic lists, but never ranked in Top or Trending, and never merged with other items by wording.
ALTER TABLE stories ADD COLUMN routine boolean NOT NULL DEFAULT false;
