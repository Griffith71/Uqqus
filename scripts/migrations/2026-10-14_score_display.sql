-- How a member reads the score of a post or comment (helpers/jinja2.py score_mode): 0, the default, is the upvotes
-- coloured by their share of the votes (helpers/vote_colour.py); 1 is upvotes minus downvotes, as scores read before.
-- Chosen when signing up, changed in Settings > Content. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-14_score_display.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS score_display smallint NOT NULL DEFAULT 0;
