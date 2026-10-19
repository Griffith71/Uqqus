-- A Space's own board, its video setting and its slow mode (helpers/spaces.py, helpers/space_store.py; Phase 2 of the
-- owner's Spaces plan, 2026-10-19). A Space's post takes no comments: its discussion is this board, public to whoever
-- may open the post and not end-to-end encrypted, so it can be rated, slowed, reported and removed. When a Space that
-- is not recorded ends, its messages go with its post (a reported one waits for the admins first). Reports name the
-- reporter for the once-per-person rule only; nobody is ever shown who reported. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-11_space_board.sql
ALTER TABLE public.spaces ADD COLUMN IF NOT EXISTS video boolean DEFAULT false NOT NULL;
ALTER TABLE public.spaces ADD COLUMN IF NOT EXISTS board_slow_seconds smallint DEFAULT 0 NOT NULL;

CREATE TABLE IF NOT EXISTS public.space_messages (
    id SERIAL PRIMARY KEY,
    space_id integer NOT NULL REFERENCES public.spaces(id) ON DELETE CASCADE,
    author_id integer NOT NULL,
    body character varying(500) DEFAULT '' NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    quote_id integer,
    created_utc integer NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL,
    removed_utc integer DEFAULT 0 NOT NULL,
    removed_by_id integer
);

CREATE INDEX IF NOT EXISTS space_messages_space_idx ON public.space_messages (space_id, id);
CREATE INDEX IF NOT EXISTS space_messages_author_idx ON public.space_messages (author_id, created_utc);

CREATE TABLE IF NOT EXISTS public.space_message_reports (
    id SERIAL PRIMARY KEY,
    message_id integer NOT NULL REFERENCES public.space_messages(id) ON DELETE CASCADE,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT space_message_reports_once UNIQUE (message_id, reporter_id)
);
