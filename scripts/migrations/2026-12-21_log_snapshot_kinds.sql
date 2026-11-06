-- Private copies say what they are copies OF (the audit of the Log, 2026-11-05, finding H2; helpers/action_log.py).
-- A snapshot carries the guild's board id even when a guildmaster has no business reading it: the copy of a comment its AUTHOR
-- deleted was opened by the guildmasters of the board the post sits on, though the plan gave them only the copy of a HIDE.
-- `kind` decides who may open a copy and for how long it is kept:
--   hide         a guild's hide of a post or comment: its author, that guild's guildmasters, admins; 90 days
--   author_hide  a post's author hiding a comment under it: the comment's author, the hider (`hider_id`), admins; 90 days
--   grace        an author's own delete, and an erase inside the grace window: the author and admins; 90 days
--   delete       (written from slice D3a) the public copy of a deleted item: no expiry, read through the Log by its entry's audience
--   legal        illegal content: level-4 admins only; one year
-- Rows made before this column are labelled from what they hold: a legal hold is `legal`, a copy an entry points at is a `hide`,
-- the rest were an author's own delete or a post author's hide of a comment (read as `grace`, the stricter of the two).
-- Safe to run twice: the label runs only on rows that have none.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-21_log_snapshot_kinds.sql
ALTER TABLE public.log_snapshots ADD COLUMN IF NOT EXISTS kind character varying(12);
ALTER TABLE public.log_snapshots ADD COLUMN IF NOT EXISTS hider_id integer;

UPDATE public.log_snapshots
SET kind = CASE WHEN legal_hold THEN 'legal' WHEN entry_id IS NOT NULL THEN 'hide' ELSE 'grace' END
WHERE kind IS NULL;

ALTER TABLE public.log_snapshots ALTER COLUMN kind SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'log_snapshots_kind' AND conrelid = 'public.log_snapshots'::regclass) THEN
        ALTER TABLE public.log_snapshots
            ADD CONSTRAINT log_snapshots_kind CHECK (kind IN ('hide', 'author_hide', 'grace', 'delete', 'legal'));
    END IF;
END $$;
