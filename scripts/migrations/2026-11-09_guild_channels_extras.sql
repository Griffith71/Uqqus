-- Guild channels, part 5 (helpers/guild_automod.py, helpers/guild_channel_feeds.py, helpers/guild_channels.py): a guild's
-- auto-moderation settings for what is written in its channels, bot accounts whose new posts go into an announcement
-- channel, and end-to-end encrypted channels (private and Circle guilds only). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-09_guild_channels_extras.sql
CREATE TABLE IF NOT EXISTS public.guild_chat_settings (
    board_id integer PRIMARY KEY REFERENCES public.boards(id) ON DELETE CASCADE,
    min_account_days integer DEFAULT 0 NOT NULL,
    min_member_days integer DEFAULT 0 NOT NULL,
    link_member_days integer DEFAULT 0 NOT NULL,
    blocked_words text DEFAULT '' NOT NULL,
    CONSTRAINT guild_chat_settings_days CHECK (min_account_days BETWEEN 0 AND 365 AND min_member_days BETWEEN 0 AND 365
                                                AND link_member_days BETWEEN 0 AND 365)
);

CREATE TABLE IF NOT EXISTS public.guild_channel_feeds (
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    bot_user_id integer NOT NULL,
    added_by integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    last_post_id integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (channel_id, bot_user_id)
);

ALTER TABLE public.guild_channels ADD COLUMN IF NOT EXISTS encrypted boolean DEFAULT false NOT NULL;
