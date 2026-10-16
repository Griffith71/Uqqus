-- Calls in chats (helpers/calls.py, helpers/call_store.py): one row per call, for history and abuse reports. Who is
-- in a call right now lives in Redis; this keeps only the room, the kind, who started it, when it started and ended
-- and how many were in it at most. Never what was said. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-23_chat_calls.sql
CREATE TABLE IF NOT EXISTS public.chat_calls (
    id SERIAL PRIMARY KEY,
    room_id character varying(255) NOT NULL,
    call_id character varying(32) NOT NULL,
    kind character varying(8) NOT NULL,
    started_by_id integer NOT NULL,
    started_utc integer NOT NULL,
    ended_utc integer DEFAULT 0 NOT NULL,
    peak smallint DEFAULT 1 NOT NULL,
    CONSTRAINT chat_calls_call_key UNIQUE (call_id),
    CONSTRAINT chat_calls_kind_check CHECK (kind IN ('audio', 'video'))
);

CREATE INDEX IF NOT EXISTS chat_calls_starter_idx ON public.chat_calls (started_by_id, started_utc);
CREATE INDEX IF NOT EXISTS chat_calls_room_idx ON public.chat_calls (room_id, started_utc);
