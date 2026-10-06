-- "Who can comment?" on a post: 0 everyone, 1 accounts the author follows,
-- 2 Premium accounts. Only read for a post on the author's own profile.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-06_comment_permission.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS comment_permission smallint NOT NULL DEFAULT 0;
