-- One edit of a post is one row PER COPY in its history (the post and each live forward carry the same text), so an admin hiding
-- a version hid one row and left the same text on every copy's history page (the audit of the Log, 2026-11-05: M1).
-- `edit_group` names the rows of one edit, so hiding a version hides it everywhere. NULL on rows made before it: those hide
-- one at a time, as they did. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-22_edit_groups.sql
ALTER TABLE public.content_edit_history ADD COLUMN IF NOT EXISTS edit_group bigint;
CREATE INDEX IF NOT EXISTS content_edit_history_group_idx ON public.content_edit_history (edit_group) WHERE edit_group IS NOT NULL;
