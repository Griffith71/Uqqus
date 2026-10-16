-- Live threads (helpers/live_threads.py, helpers/live_thread_store.py): a post its author marks Live, with timestamped
-- updates above its comments. Kept per primary post, so forwarded copies show the same updates. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-26_live_threads.sql
CREATE TABLE IF NOT EXISTS public.live_threads (
    post_id integer PRIMARY KEY,
    started_utc integer NOT NULL,
    ended_utc integer DEFAULT 0 NOT NULL,
    last_update_utc integer DEFAULT 0 NOT NULL,
    started_by_id integer NOT NULL
);

CREATE TABLE IF NOT EXISTS public.live_updates (
    id SERIAL PRIMARY KEY,
    post_id integer NOT NULL REFERENCES public.live_threads(post_id) ON DELETE CASCADE,
    author_id integer NOT NULL,
    body text NOT NULL,
    body_html text NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    created_utc integer NOT NULL,
    edited_utc integer DEFAULT 0 NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS live_updates_post_idx ON public.live_updates (post_id, id);
