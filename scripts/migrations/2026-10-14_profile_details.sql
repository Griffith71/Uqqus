-- The X-style profile header (templates/partials/profile_header.html): details a member writes about themselves,
-- who may see each of their lists (helpers/follow_lists.py) and reports of an account (helpers/user_reports.py).
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-14_profile_details.sql

-- Job, location and website, shown on the profile (helpers/profile_details.py cleans them; the word filter rates job
-- and location with the bio, into bio_severity).
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_job character varying(60) DEFAULT '' NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_location character varying(60) DEFAULT '' NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_website character varying(200) DEFAULT '' NOT NULL;

-- Who may open each list: 0 everyone, 1 the account's followers, 2 only the account. Existing accounts start at 2,
-- so nothing that was private becomes public; sign-up sets 0 on a new account (routes/login.py).
ALTER TABLE users ADD COLUMN IF NOT EXISTS following_visibility smallint DEFAULT 2 NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS followers_visibility smallint DEFAULT 2 NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscribers_visibility smallint DEFAULT 2 NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscriptions_visibility smallint DEFAULT 2 NOT NULL;

-- A report of an account (not a post or comment): once per person per account, worked at /admin/user_reports.
CREATE TABLE IF NOT EXISTS user_reports (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT user_reports_once UNIQUE (user_id, reporter_id)
);
CREATE INDEX IF NOT EXISTS user_reports_open_idx ON user_reports (user_id) WHERE resolved_utc = 0;
CREATE INDEX IF NOT EXISTS user_reports_reporter_idx ON user_reports (reporter_id, created_utc);
