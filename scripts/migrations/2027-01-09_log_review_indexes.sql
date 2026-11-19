-- Indexes the Log's newer queries need (the second review of the Log, 2026-11-17, C54). Safe to run twice.
--   log_snapshots(author_id)     a member's own copies (History > Anonymous > Deleted, the account export, the per-author grace count)
--   submissions(repost_id)       a post's forwarded copies: every delete, anonymise, family_ids and thread tab reads `WHERE repost_id = :primary`
--   modactions(created_utc)      the reconcile step's "rows with no entry yet" read and the guild logs' newest-first pages
-- A live site with a big `submissions` table builds the second one by hand, outside a transaction and without locking writes, before applying this file (it then finds it there).
CREATE INDEX IF NOT EXISTS log_snapshots_author_idx ON public.log_snapshots (author_id) WHERE author_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS submissions_repost_id_idx ON public.submissions (repost_id);
CREATE INDEX IF NOT EXISTS modactions_created_utc_idx ON public.modactions (created_utc);
