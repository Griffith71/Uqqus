-- Sensitive accounts and an All that leaves sensitive content out by default (helpers/sensitive.py).
-- An account (like a guild already) can be marked sensitive: upsetting topics, a heads-up rather than a rule break.
-- Admins can force it on an account or a guild, and then its owner can't switch it off. A viewer chooses whether
-- All and For You show sensitive content (off by default; visitors never). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-18_sensitive_accounts.sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_sensitive boolean DEFAULT false NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sensitive_forced boolean DEFAULT false NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS all_shows_sensitive boolean DEFAULT false NOT NULL;
ALTER TABLE boards ADD COLUMN IF NOT EXISTS sensitive_forced boolean DEFAULT false NOT NULL;

-- the "sensitive authors" subquery All and For You run is over a handful of rows
CREATE INDEX IF NOT EXISTS users_sensitive_idx ON users (id) WHERE is_sensitive;
