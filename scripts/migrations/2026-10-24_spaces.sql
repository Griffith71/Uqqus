-- Spaces: live audio rooms (helpers/spaces.py, helpers/space_store.py). A Space is a post: this row holds when it
-- starts, its state, whether it is recorded and its replay (the host's own linked storage). Who is in it right now
-- lives in Redis. Reports name the reporter for the once-per-person rule only; nobody is ever shown who reported.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-24_spaces.sql
CREATE TABLE IF NOT EXISTS public.spaces (
    id SERIAL PRIMARY KEY,
    post_id integer NOT NULL,
    host_id integer NOT NULL,
    status character varying(9) DEFAULT 'scheduled' NOT NULL,
    scheduled_utc integer NOT NULL,
    started_utc integer DEFAULT 0 NOT NULL,
    ended_utc integer DEFAULT 0 NOT NULL,
    record boolean DEFAULT false NOT NULL,
    replay_asset_id integer,
    listener_peak smallint DEFAULT 0 NOT NULL,
    ended_by_admin boolean DEFAULT false NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    matrix_room_id character varying(255),
    CONSTRAINT spaces_post_key UNIQUE (post_id),
    CONSTRAINT spaces_status_check CHECK (status IN ('scheduled', 'live', 'ended'))
);

-- a silent Matrix room per Space: being in a room together is how the browsers learn each other's devices
ALTER TABLE public.spaces ADD COLUMN IF NOT EXISTS matrix_room_id character varying(255);

CREATE INDEX IF NOT EXISTS spaces_host_idx ON public.spaces (host_id, created_utc);
CREATE INDEX IF NOT EXISTS spaces_live_idx ON public.spaces (status) WHERE status = 'live';

CREATE TABLE IF NOT EXISTS public.space_roles (
    space_id integer NOT NULL REFERENCES public.spaces(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    role character varying(8) NOT NULL,
    status character varying(8) NOT NULL,
    updated_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (space_id, user_id),
    CONSTRAINT space_roles_role_check CHECK (role IN ('cohost', 'speaker')),
    CONSTRAINT space_roles_status_check CHECK (status IN ('invited', 'accepted'))
);

CREATE TABLE IF NOT EXISTS public.space_hands (
    space_id integer NOT NULL REFERENCES public.spaces(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    raised_utc integer NOT NULL,
    PRIMARY KEY (space_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.space_reminders (
    space_id integer NOT NULL REFERENCES public.spaces(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    created_utc integer NOT NULL,
    PRIMARY KEY (space_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.space_removed (
    space_id integer NOT NULL REFERENCES public.spaces(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    removed_by_id integer NOT NULL,
    created_utc integer NOT NULL,
    PRIMARY KEY (space_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.space_reports (
    id SERIAL PRIMARY KEY,
    space_id integer NOT NULL REFERENCES public.spaces(id) ON DELETE CASCADE,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT space_reports_once UNIQUE (space_id, reporter_id)
);
