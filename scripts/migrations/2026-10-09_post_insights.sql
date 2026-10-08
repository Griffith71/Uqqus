-- Post insights (helpers/insights.py, helpers/insights_store.py): how many times a post page was opened,
-- per day. Only a count per post and day: no viewer, address or account is stored here (the
-- "has this viewer opened it in the last half hour" check lives in Redis and expires on its own).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-09_post_insights.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS post_view_days (
    id SERIAL PRIMARY KEY,
    post_id integer NOT NULL,
    day integer NOT NULL,
    views integer DEFAULT 0 NOT NULL,
    CONSTRAINT post_view_days_key UNIQUE (post_id, day)
);

CREATE INDEX IF NOT EXISTS post_view_days_day_index ON post_view_days (day);
