-- The Log: documentation for a sanction (the owner, 2026-11-14: exiles and admin removals need a reason with supporting references and documentation of the
-- content that justified them). A new kind of private copy, `evidence`: the content an exile cites (up to five posts or comments of the exiled account in
-- that guild), kept as it was when the decision was made, so the person, the deciding guild's guildmasters and the admins can read what was relied on after
-- the author edits or deletes it. One year (the length of an appeal's relevance), then the text is emptied like any copy; the row and the entry stay.
-- Only the CHECK on `log_snapshots.kind` changes, and only while it lacks `evidence`: running this twice changes nothing.
--   psql "$DATABASE_URL" -f scripts/migrations/2027-01-08_evidence.sql
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'log_snapshots_kind' AND conrelid = 'public.log_snapshots'::regclass
                   AND pg_get_constraintdef(oid) LIKE '%evidence%') THEN
        ALTER TABLE public.log_snapshots DROP CONSTRAINT IF EXISTS log_snapshots_kind;
        ALTER TABLE public.log_snapshots
            ADD CONSTRAINT log_snapshots_kind CHECK (kind IN ('hide', 'author_hide', 'grace', 'delete', 'legal', 'evidence'));
    END IF;
END $$;
