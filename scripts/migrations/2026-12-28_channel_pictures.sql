-- A picture in a public broadcast channel (helpers/broadcasts.py PICTURE_KEY): the message keeps which uploaded picture it
-- showed, so removing the message takes the picture down too. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-28_channel_pictures.sql
ALTER TABLE public.broadcast_posts ADD COLUMN IF NOT EXISTS media_asset_id integer;
