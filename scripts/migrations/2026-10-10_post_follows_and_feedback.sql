-- Follow a post (helpers/post_follows.py) and "Show fewer posts like this" (helpers/post_feedback.py).
-- No foreign keys to users, as `stories`. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_post_follows_and_feedback.sql

CREATE TABLE IF NOT EXISTS public.post_follows (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    post_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT post_follows_pair_key UNIQUE (user_id, post_id)
);

CREATE INDEX IF NOT EXISTS post_follows_post_idx ON public.post_follows USING btree (post_id);

-- post_id is the PRIMARY post (repost_id or id), so the original and its copies leave together.
-- author_id is NULL for an anonymous post: the author is never recoverable from this row.
CREATE TABLE IF NOT EXISTS public.post_feedback (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    post_id integer NOT NULL,
    board_id integer,
    author_id integer,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT post_feedback_pair_key UNIQUE (user_id, post_id)
);

CREATE INDEX IF NOT EXISTS post_feedback_user_idx ON public.post_feedback USING btree (user_id, created_utc DESC);
