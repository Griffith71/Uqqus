-- Broadcast channels (helpers/broadcasts.py, helpers/broadcast_store.py): one Matrix room per channel, made by the
-- site's bot. A public channel's messages go through the site and are kept here (word filter, daily count, reports);
-- a channel for circles is end-to-end encrypted and the site keeps none of its messages. Reports name the reporter
-- for the once-per-person rule only; nobody is ever shown who reported. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-25_broadcasts.sql
CREATE TABLE IF NOT EXISTS public.broadcasts (
    id SERIAL PRIMARY KEY,
    owner_id integer NOT NULL,
    name character varying(50) NOT NULL,
    name_severity smallint DEFAULT 0 NOT NULL,
    audience smallint DEFAULT 0 NOT NULL,
    matrix_room_id character varying(255) NOT NULL,
    created_utc integer NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT broadcasts_room_key UNIQUE (matrix_room_id),
    CONSTRAINT broadcasts_audience_check CHECK (audience IN (0, 4))
);

CREATE INDEX IF NOT EXISTS broadcasts_owner_idx ON public.broadcasts (owner_id);

CREATE TABLE IF NOT EXISTS public.broadcast_circles (
    broadcast_id integer NOT NULL REFERENCES public.broadcasts(id) ON DELETE CASCADE,
    circle_id integer NOT NULL,
    PRIMARY KEY (broadcast_id, circle_id)
);

CREATE TABLE IF NOT EXISTS public.broadcast_members (
    broadcast_id integer NOT NULL REFERENCES public.broadcasts(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    role character varying(8) DEFAULT 'member' NOT NULL,
    joined_utc integer NOT NULL,
    unread_count integer DEFAULT 0 NOT NULL,
    last_read_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (broadcast_id, user_id),
    CONSTRAINT broadcast_members_role_check CHECK (role IN ('owner', 'helper', 'member'))
);

CREATE INDEX IF NOT EXISTS broadcast_members_user_idx ON public.broadcast_members (user_id);

CREATE TABLE IF NOT EXISTS public.broadcast_posts (
    id SERIAL PRIMARY KEY,
    broadcast_id integer NOT NULL REFERENCES public.broadcasts(id) ON DELETE CASCADE,
    author_id integer NOT NULL,
    event_id character varying(255) NOT NULL,
    body text NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    created_utc integer NOT NULL,
    removed_utc integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS broadcast_posts_channel_idx ON public.broadcast_posts (broadcast_id, created_utc);
CREATE INDEX IF NOT EXISTS broadcast_posts_event_idx ON public.broadcast_posts (broadcast_id, event_id);

CREATE TABLE IF NOT EXISTS public.broadcast_reports (
    id SERIAL PRIMARY KEY,
    broadcast_id integer NOT NULL REFERENCES public.broadcasts(id) ON DELETE CASCADE,
    post_id integer NOT NULL REFERENCES public.broadcast_posts(id) ON DELETE CASCADE,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT broadcast_reports_once UNIQUE (post_id, reporter_id)
);
