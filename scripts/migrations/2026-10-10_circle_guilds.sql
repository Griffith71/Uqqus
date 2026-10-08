-- Circle guilds (helpers/circles.py): a guild whose members, and only they, see its posts and post straight into it.
-- A Circle guild is also a private guild (is_private), so the existing private-guild rules do most of the work.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_circle_guilds.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE boards ADD COLUMN IF NOT EXISTS is_circle boolean NOT NULL DEFAULT false;
