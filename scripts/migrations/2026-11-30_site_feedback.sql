-- Feedback from members about the site itself (a bug, how something looks, an idea): the "Send feedback" sheet
-- (templates/partials/site_feedback_modal.html, POST /api/feedback, helpers/site_feedback.py) and the admins' inbox
-- (/admin/feedback). Not the same as post_feedback ("Show fewer posts like this") or the reports of a post or an account.
-- `context` is a small JSON text of what the page sent along (path, theme, density, screen size, language, browser);
-- never an address. `resolved_utc` is set while `status` is fixed or declined; a screenshot is deleted a month later
-- (helpers/retention.py). Screenshots sit in their own table so the inbox list never reads their bytes.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-30_site_feedback.sql

CREATE TABLE IF NOT EXISTS site_feedback (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    kind character varying(16) DEFAULT 'other' NOT NULL,
    message character varying(2000) DEFAULT '' NOT NULL,
    page character varying(300) DEFAULT '' NOT NULL,
    context text DEFAULT '' NOT NULL,
    status character varying(16) DEFAULT 'open' NOT NULL,
    response character varying(1000) DEFAULT '' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS site_feedback_status_idx ON site_feedback (status, created_utc);
CREATE INDEX IF NOT EXISTS site_feedback_user_idx ON site_feedback (user_id, created_utc);

CREATE TABLE IF NOT EXISTS site_feedback_images (
    feedback_id integer PRIMARY KEY,
    mime character varying(32) NOT NULL,
    data bytea NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL
);
