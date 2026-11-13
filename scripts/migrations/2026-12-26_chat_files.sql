-- Files sent in end-to-end encrypted chats (helpers/media/rules.py CHAT): the browser encrypts them before they leave,
-- they go to the sender's own linked storage like a picture, and only the members of the chat they were sent in may
-- fetch them. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-26_chat_files.sql
ALTER TABLE public.media_assets ADD COLUMN IF NOT EXISTS chat_room_id character varying(255);
CREATE INDEX IF NOT EXISTS media_assets_chat_room_idx ON public.media_assets (chat_room_id) WHERE chat_room_id IS NOT NULL;
