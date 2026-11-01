-- The age declaration (helpers/age_declaration.py; the owner, 2026-10-30): sign-up asks, and an existing member is asked
-- once by a notice. `age_declared` is NULL (not asked yet: a member from before this, a bot, the site's account), 1 (18 or
-- over) or 2 (a child account: a parent or guardian set it up for their child, 13 to 17); `age_declared_utc` is when.
-- This is a declaration, not a check of anyone's age. Nothing changes for anyone until they answer.
--
-- A child account stays on the Child word filter and never shows sensitive content in All or For You. The routes refuse
-- first (routes/settings.py); this constraint is what makes it hold for anything that writes to the table, a route added
-- later included. Only an admin (level 3) changes an answer. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-10_age_declaration.sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS age_declared smallint;
ALTER TABLE users ADD COLUMN IF NOT EXISTS age_declared_utc integer DEFAULT 0 NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_age_declared_rule') THEN
        ALTER TABLE users ADD CONSTRAINT users_age_declared_rule CHECK (
            age_declared IS NULL OR age_declared = 1
            OR (age_declared = 2 AND COALESCE(filter_level, 1) = 2 AND NOT all_shows_sensitive));
    END IF;
END $$;
