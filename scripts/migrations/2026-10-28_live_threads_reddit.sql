-- Live threads that read like Reddit's (helpers/live_threads.py, 2026-10-16): a chosen length (`ends_utc`; 0 for a
-- thread started before, which keeps the old rule), a pinned summary the runners keep up to date, and updates that can
-- be crossed out ("Corrected"). Who is watching is never stored here (a count in Redis only). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-28_live_threads_reddit.sql
ALTER TABLE public.live_threads ADD COLUMN IF NOT EXISTS ends_utc integer DEFAULT 0 NOT NULL;
ALTER TABLE public.live_threads ADD COLUMN IF NOT EXISTS summary text DEFAULT '' NOT NULL;
ALTER TABLE public.live_threads ADD COLUMN IF NOT EXISTS summary_html text DEFAULT '' NOT NULL;
ALTER TABLE public.live_threads ADD COLUMN IF NOT EXISTS summary_severity smallint DEFAULT 0 NOT NULL;
ALTER TABLE public.live_threads ADD COLUMN IF NOT EXISTS summary_edited_utc integer DEFAULT 0 NOT NULL;
ALTER TABLE public.live_updates ADD COLUMN IF NOT EXISTS struck_utc integer DEFAULT 0 NOT NULL;
