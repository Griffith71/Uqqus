-- A post an admin forced sensitive: its author and the guild's guildmasters can't switch it off, as for an account and a guild
-- (`users.sensitive_forced`, `boards.sensitive_forced`). Kept on the post a guild copy copies (routes/settings.py
-- admin_sensitive_post, Submission.sensitive_lock). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-11_post_sensitive_forced.sql
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS sensitive_forced boolean DEFAULT false NOT NULL;
