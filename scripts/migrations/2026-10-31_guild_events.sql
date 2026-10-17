-- Guild events and their calendar (helpers/guild_events.py, helpers/guild_event_store.py): a guild's events and its
-- members' answers (Going or Interested; only ever counted, never listed). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-31_guild_events.sql
CREATE TABLE IF NOT EXISTS public.guild_events (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    creator_id integer NOT NULL,
    title character varying(120) NOT NULL,
    details text DEFAULT '' NOT NULL,
    details_html text DEFAULT '' NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    starts_utc integer NOT NULL,
    ends_utc integer NOT NULL,
    location_url character varying(300) DEFAULT '' NOT NULL,
    location_text character varying(120) DEFAULT '' NOT NULL,
    space_post_id integer,
    cancelled_utc integer DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    reminded_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT guild_events_times CHECK (ends_utc > starts_utc)
);

CREATE INDEX IF NOT EXISTS guild_events_board_idx ON public.guild_events (board_id, starts_utc);
CREATE INDEX IF NOT EXISTS guild_events_due_idx ON public.guild_events (starts_utc) WHERE reminded_utc = 0 AND cancelled_utc = 0;

CREATE TABLE IF NOT EXISTS public.guild_event_rsvps (
    event_id integer NOT NULL REFERENCES public.guild_events(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    status character varying(12) NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (event_id, user_id),
    CONSTRAINT guild_event_rsvps_status CHECK (status IN ('going', 'interested'))
);
