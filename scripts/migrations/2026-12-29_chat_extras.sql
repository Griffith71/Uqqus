-- Chat extras (CLAUDE.md "Chat extras"): whether a member shows others they read and type ("Seen"), each 1:1 chat's and
-- group's disappearing-message timer (0 off, else seconds), and the messages waiting to disappear (only an event id and
-- when, never what it says: the site can't read it). No foreign keys to users. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-29_chat_extras.sql
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS chat_receipts boolean DEFAULT true NOT NULL;
ALTER TABLE public.chat_conversations ADD COLUMN IF NOT EXISTS disappear_seconds integer DEFAULT 0 NOT NULL;
ALTER TABLE public.chat_groups ADD COLUMN IF NOT EXISTS disappear_seconds integer DEFAULT 0 NOT NULL;
ALTER TABLE public.media_assets ADD COLUMN IF NOT EXISTS expires_utc integer DEFAULT 0 NOT NULL;
CREATE TABLE IF NOT EXISTS public.chat_expiring (
    id SERIAL PRIMARY KEY,
    room_id character varying(255) NOT NULL,
    event_id character varying(255) NOT NULL,
    expires_utc integer NOT NULL,
    tries integer DEFAULT 0 NOT NULL,
    CONSTRAINT chat_expiring_event_key UNIQUE (room_id, event_id)
);
CREATE INDEX IF NOT EXISTS chat_expiring_due_idx ON public.chat_expiring (expires_utc);
CREATE INDEX IF NOT EXISTS media_assets_expires_idx ON public.media_assets (expires_utc) WHERE expires_utc > 0;
