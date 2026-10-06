-- Content disclosure on posts and comments: "Paid partnership" and "Made with AI".
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-06_content_disclosure.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS paid_partnership boolean NOT NULL DEFAULT false;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS made_with_ai boolean NOT NULL DEFAULT false;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS paid_partnership boolean NOT NULL DEFAULT false;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS made_with_ai boolean NOT NULL DEFAULT false;
