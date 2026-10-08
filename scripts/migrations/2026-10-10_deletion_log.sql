-- The site-wide deletion log (/log/deleted) reads the newest 'delete' rows of content_edit_history:
-- a partial index keeps that a short walk however long the table of edits grows.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_deletion_log.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE INDEX IF NOT EXISTS content_edit_history_deletes_idx ON content_edit_history USING btree (id DESC) WHERE action = 'delete';
