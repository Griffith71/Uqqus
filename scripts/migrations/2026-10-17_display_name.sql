-- A display name, as on x.com: free text a member sets and changes at any time (Settings > Profile), drawn first with
-- the unchanging @handle beside it. The username stays the unique handle and keeps its coin cost and 7-day wait.
-- helpers/display_names.py cleans it; the word filter rates it with the username, into name_severity.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-17_display_name.sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name character varying(50) DEFAULT '' NOT NULL;
