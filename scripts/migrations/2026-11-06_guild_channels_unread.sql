-- Guild channels: unread by a counter, not a clock (helpers/guild_channel_store.py). A channel counts its messages
-- (`activity_seq`, one UPDATE per message however many people are in the guild) and a person's row holds the count they
-- last read (`last_read_seq`): a message sent in the same second as a read is still news, which whole seconds can't say.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-06_guild_channels_unread.sql
ALTER TABLE public.guild_channels ADD COLUMN IF NOT EXISTS activity_seq bigint DEFAULT 0 NOT NULL;
ALTER TABLE public.guild_channel_reads ADD COLUMN IF NOT EXISTS last_read_seq bigint DEFAULT 0 NOT NULL;
