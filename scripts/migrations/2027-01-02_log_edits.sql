-- The Log, slice D3b: what a member edits stays in the Log (the owner's decisions 4 and 19 of 2026-11-05; helpers/action_log.py, routes/log_pages.py).
--   log_entries.log_type   gains `edit`: an author's own edit of a post or a comment after the five-minute grace window (an edit inside it is part of
--                          drafting it and is not recorded). One entry per edit, whatever the number of guild copies it reached; its reason is the edit
--                          summary; the versions themselves are on the item's history page, where an admin can hide one.
-- The Edits tab groups an item's consecutive edits when it draws them: the table stays one row per edit.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2027-01-02_log_edits.sql
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'log_entries_type' AND conrelid = 'public.log_entries'::regclass
                   AND pg_get_constraintdef(oid) LIKE '%''edit''%') THEN
        ALTER TABLE public.log_entries DROP CONSTRAINT IF EXISTS log_entries_type;
        ALTER TABLE public.log_entries ADD CONSTRAINT log_entries_type CHECK (log_type IN ('content', 'sanction', 'guild', 'suppression', 'deletion', 'edit'));
    END IF;
END $$;
