-- Bot feeds are read as often as they safely can be, not every 30 minutes (helpers/bots.py next_interval): never
-- more often than the floor (5 minutes) or than the feed asks (RSS <ttl>, sy:updatePeriod, HTTP max-age), and less
-- often while a feed has nothing new. asked_minutes keeps what the feed last asked, since a 304 answer has no body.
-- check_minutes is now the current interval; existing feeds move to the new rule from their next read.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-19_feed_intervals.sql
ALTER TABLE bot_sources ADD COLUMN IF NOT EXISTS asked_minutes smallint DEFAULT 0 NOT NULL;
ALTER TABLE bot_sources ALTER COLUMN check_minutes SET DEFAULT 5;
