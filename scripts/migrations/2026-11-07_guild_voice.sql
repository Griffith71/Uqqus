-- Guild channels, part 3 (helpers/guild_channels.py): voice channels (a persistent room to drop in and out of, on the
-- site's call relay) and stage channels (a channel that shows a Space). A voice channel's Matrix room is the silent
-- encrypted one calls and Spaces use to learn each other's devices; a stage channel has no room (its row holds a
-- placeholder). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-07_guild_voice.sql
ALTER TABLE public.guild_channels DROP CONSTRAINT IF EXISTS guild_channels_kind_check;
ALTER TABLE public.guild_channels ADD CONSTRAINT guild_channels_kind_check CHECK (kind IN ('text', 'announcement', 'voice', 'stage'));
ALTER TABLE public.guild_channels ADD COLUMN IF NOT EXISTS space_post_id integer;
