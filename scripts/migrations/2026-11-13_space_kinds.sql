-- Spaces as their own thing (the owner, 2026-10-20; helpers/spaces.py): a Space is exactly one of three kinds, a
-- message board, a live thread or a call, chosen when it is made and never changed. A board and a live thread run for
-- a length the host picks (`ends_utc`); a call keeps its 4-hour limit. A recorded call with cameras keeps a video
-- replay on the host's own video service (`replay_link`, a YouTube or Dailymotion address). A Space is not a post to
-- the member: its hidden backing row is marked `submissions.is_space`, so a profile's Posts leave it out cheaply and
-- its address is the Space's own. Every Space made before is a call. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-13_space_kinds.sql
ALTER TABLE public.spaces ADD COLUMN IF NOT EXISTS kind character varying(5) DEFAULT 'call' NOT NULL;
ALTER TABLE public.spaces ADD COLUMN IF NOT EXISTS ends_utc integer DEFAULT 0 NOT NULL;
ALTER TABLE public.spaces ADD COLUMN IF NOT EXISTS replay_link character varying(255);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'spaces_kind_check') THEN
        ALTER TABLE public.spaces ADD CONSTRAINT spaces_kind_check CHECK (kind IN ('board', 'live', 'call'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS spaces_due_idx ON public.spaces (ends_utc) WHERE status = 'live' AND ends_utc > 0;

ALTER TABLE public.submissions ADD COLUMN IF NOT EXISTS is_space boolean DEFAULT false NOT NULL;

-- the backing rows of every Space and their guild copies
UPDATE public.submissions SET is_space = true
WHERE is_space = false
  AND (id IN (SELECT post_id FROM public.spaces) OR repost_id IN (SELECT post_id FROM public.spaces));
