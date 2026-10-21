-- Guild channels, part 2 (helpers/guild_channels.py): threads (a message's thread root), categories, pinned messages,
-- a member's own mute of a channel, accepting a channel's rules before writing, and @mentions. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-05_guild_channels_polish.sql
ALTER TABLE public.guild_channel_posts ADD COLUMN IF NOT EXISTS thread_event_id character varying(255) DEFAULT '' NOT NULL;

CREATE INDEX IF NOT EXISTS guild_channel_posts_thread_idx ON public.guild_channel_posts (channel_id, thread_event_id) WHERE thread_event_id <> '';
CREATE INDEX IF NOT EXISTS guild_channel_posts_time_idx ON public.guild_channel_posts (channel_id, created_utc);

CREATE TABLE IF NOT EXISTS public.guild_channel_categories (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    name character varying(40) NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    position integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS guild_channel_categories_board_idx ON public.guild_channel_categories (board_id, position, id);

-- The column and its foreign key are separate statements: "ADD COLUMN IF NOT EXISTS ... REFERENCES" adds the
-- constraint again on every run (a second, identical foreign key named ..._fkey1).
ALTER TABLE public.guild_channels ADD COLUMN IF NOT EXISTS category_id integer;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.guild_channels'::regclass AND contype = 'f'
                   AND confrelid = 'public.guild_channel_categories'::regclass) THEN
        ALTER TABLE public.guild_channels ADD FOREIGN KEY (category_id)
            REFERENCES public.guild_channel_categories(id) ON DELETE SET NULL;
    END IF;
END $$;
ALTER TABLE public.guild_channels ADD COLUMN IF NOT EXISTS rules_gate boolean DEFAULT false NOT NULL;

CREATE TABLE IF NOT EXISTS public.guild_channel_pins (
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    post_id integer NOT NULL REFERENCES public.guild_channel_posts(id) ON DELETE CASCADE,
    pinned_by integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (channel_id, post_id)
);

CREATE TABLE IF NOT EXISTS public.guild_channel_prefs (
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    muted_until integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (channel_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.guild_channel_rules_ack (
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    rules_key character varying(32) NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (channel_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.guild_channel_mentions (
    id SERIAL PRIMARY KEY,
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    post_id integer NOT NULL REFERENCES public.guild_channel_posts(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    read_utc integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS guild_channel_mentions_user_idx ON public.guild_channel_mentions (user_id, channel_id) WHERE read_utc = 0;
