-- A curation's own algorithm (helpers/feed_algorithm.py): which posts it shows and how it
-- orders them, as JSON. '{}' means what a curation always did.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-07_curation_algorithm.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE curations ADD COLUMN IF NOT EXISTS algorithm text DEFAULT '{}' NOT NULL;
