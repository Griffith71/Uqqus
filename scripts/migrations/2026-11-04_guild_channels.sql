-- Guild channels (helpers/guild_channels.py, helpers/guild_channel_store.py): text and announcement channels inside a
-- guild, each one a Matrix room made by the site's bot and NOT end-to-end encrypted, so the word filter, slow mode,
-- reports and removals work. Messages go through the site and are kept here only for reports, the daily count and
-- removals (their text is blanked after 90 days). There is no row per member of a channel: `guild_channel_reads` has a
-- row only for people who opened it (that is also who is in its Matrix room), and unread is a date against the
-- channel's last activity, so a message costs one UPDATE however many people are in the guild. Reports name the
-- reporter for the once-per-person rule only; nobody is ever shown who reported. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-04_guild_channels.sql
CREATE TABLE IF NOT EXISTS public.guild_channels (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    name character varying(40) NOT NULL,
    topic character varying(200) DEFAULT '' NOT NULL,
    rules character varying(1000) DEFAULT '' NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    kind character varying(12) DEFAULT 'text' NOT NULL,
    read_level character varying(10) DEFAULT 'members' NOT NULL,
    write_level character varying(10) DEFAULT 'members' NOT NULL,
    slow_mode_seconds integer DEFAULT 0 NOT NULL,
    position integer DEFAULT 0 NOT NULL,
    matrix_room_id character varying(255) NOT NULL,
    last_activity_utc integer DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT guild_channels_room_key UNIQUE (matrix_room_id),
    CONSTRAINT guild_channels_kind_check CHECK (kind IN ('text', 'announcement', 'voice')),
    CONSTRAINT guild_channels_read_check CHECK (read_level IN ('members', 'approved', 'mods')),
    CONSTRAINT guild_channels_write_check CHECK (write_level IN ('members', 'approved', 'mods')),
    CONSTRAINT guild_channels_slow_check CHECK (slow_mode_seconds >= 0 AND slow_mode_seconds <= 21600)
);

CREATE INDEX IF NOT EXISTS guild_channels_board_idx ON public.guild_channels (board_id, position, id) WHERE deleted_utc = 0;

CREATE TABLE IF NOT EXISTS public.guild_channel_reads (
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    joined_utc integer DEFAULT 0 NOT NULL,
    last_read_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (channel_id, user_id)
);

CREATE INDEX IF NOT EXISTS guild_channel_reads_user_idx ON public.guild_channel_reads (user_id);

CREATE TABLE IF NOT EXISTS public.guild_channel_posts (
    id SERIAL PRIMARY KEY,
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    author_id integer NOT NULL,
    event_id character varying(255) NOT NULL,
    body text DEFAULT '' NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    removed_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT guild_channel_posts_event_key UNIQUE (channel_id, event_id)
);

CREATE INDEX IF NOT EXISTS guild_channel_posts_author_idx ON public.guild_channel_posts (channel_id, author_id, created_utc);

CREATE TABLE IF NOT EXISTS public.guild_channel_reports (
    id SERIAL PRIMARY KEY,
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    post_id integer NOT NULL REFERENCES public.guild_channel_posts(id) ON DELETE CASCADE,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT guild_channel_reports_once UNIQUE (post_id, reporter_id)
);

CREATE INDEX IF NOT EXISTS guild_channel_reports_open_idx ON public.guild_channel_reports (channel_id) WHERE resolved_utc = 0;
