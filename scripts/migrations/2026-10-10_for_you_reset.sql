-- For You settings (Settings > Content): the moment a member last pressed "Reset For You".
-- Only the date is kept; the feed does not read it yet (see CLAUDE.md, "For You settings").
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_for_you_reset.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE users ADD COLUMN IF NOT EXISTS for_you_reset_utc integer NOT NULL DEFAULT 0;
