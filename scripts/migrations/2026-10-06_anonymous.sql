-- Anonymous posts and comments: the author's real id stays on the row, but only
-- the author and site admins are told who it is (ruqqus/helpers/anonymity.py).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-06_anonymous.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS is_anonymous boolean NOT NULL DEFAULT false;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS is_anonymous boolean NOT NULL DEFAULT false;
