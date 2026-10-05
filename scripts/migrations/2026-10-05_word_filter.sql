-- Word filter (Standard + Child): storage.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-05_word_filter.sql
-- then re-rate existing content with
--   PYTHONPATH=. python scripts/rescan_word_filter.py
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

-- viewer setting: 0 Off, 1 Standard (default), 2 Child. Replaces hide_offensive.
ALTER TABLE users ADD COLUMN IF NOT EXISTS filter_level smallint NOT NULL DEFAULT 1;
UPDATE users SET filter_level = 0 WHERE hide_offensive = false AND filter_level = 1;

-- how offensive the text is: 0 clean, 1 profanity (Child hides), 2 extreme (Standard hides)
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS word_severity smallint NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS word_filter_version character varying(12);
ALTER TABLE comments ADD COLUMN IF NOT EXISTS word_severity smallint NOT NULL DEFAULT 0;
ALTER TABLE comments ADD COLUMN IF NOT EXISTS word_filter_version character varying(12);
ALTER TABLE users ADD COLUMN IF NOT EXISTS name_severity smallint NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS bio_severity smallint NOT NULL DEFAULT 0;
ALTER TABLE boards ADD COLUMN IF NOT EXISTS name_severity smallint NOT NULL DEFAULT 0;
ALTER TABLE boards ADD COLUMN IF NOT EXISTS description_severity smallint NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS submissions_word_severity_index ON submissions USING btree (word_severity);
CREATE INDEX IF NOT EXISTS comments_word_severity_index ON comments USING btree (word_severity);

-- the word list itself (maintained from /admin/word_filter)
CREATE TABLE IF NOT EXISTS word_filter_entries (
    id SERIAL PRIMARY KEY,
    word character varying(64) NOT NULL,
    severity smallint NOT NULL DEFAULT 1,
    mode character varying(16) NOT NULL DEFAULT 'word',
    variants character varying(512) DEFAULT '',
    suffixes character varying(512) DEFAULT NULL,
    enabled boolean NOT NULL DEFAULT true,
    note character varying(256) DEFAULT '',
    created_utc integer DEFAULT 0,
    CONSTRAINT word_filter_entries_word_key UNIQUE (word)
);
