-- The public Log (the owner, 2026-10-31; helpers/action_log.py, routes/log_pages.py): the second slice of the Log.
--   1. The text of what a person deleted, or a guild's guildmasters hid, or the post's author hid, is no longer public. It used
--      to sit in content_edit_history.previous_* where /log/deleted and the item's history page showed it to everyone. It moves
--      to a private snapshot (log_snapshots: the item's author, admins and, for a guild hide, that guild's guildmasters) kept
--      90 days from the action, and the history row keeps no text. Rows of items an admin purged or obliterated have no text to
--      move (they were scrubbed); an action older than 90 days is simply emptied, as its snapshot would already be.
--      The history of an EDIT stays: the versions edited away are the item's public page history.
--   2. A revision of an item's history can be hidden by an admin (Wikipedia's revision deletion): the row stays, its text
--      is emptied and hidden_utc / hidden_by say so.
-- Safe to run twice: the move only touches rows that still hold text, and the second run finds none.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-14_log_public.sql
ALTER TABLE public.content_edit_history ADD COLUMN IF NOT EXISTS hidden_utc bigint DEFAULT 0 NOT NULL;
ALTER TABLE public.content_edit_history ADD COLUMN IF NOT EXISTS hidden_by integer;

INSERT INTO public.log_snapshots (author_id, board_id, target_submission_id, target_comment_id, title, url, body, body_html,
                                  created_utc, expires_utc)
SELECT s.author_id, h.board_id, h.target_submission_id, NULL, h.previous_title, h.previous_url, h.previous_body,
       h.previous_body_html, h.created_utc, h.created_utc + 7776000
FROM public.content_edit_history h
JOIN public.submissions s ON s.id = h.target_submission_id AND s.purged_utc = 0
WHERE h.action IN ('delete', 'guild_hide', 'author_hide') AND h.target_submission_id > 0
  AND (h.previous_title IS NOT NULL OR h.previous_body IS NOT NULL OR h.previous_body_html IS NOT NULL)
  AND h.created_utc > extract(epoch from now())::bigint - 7776000;

INSERT INTO public.log_snapshots (author_id, board_id, target_submission_id, target_comment_id, title, url, body, body_html,
                                  created_utc, expires_utc)
SELECT c.author_id, h.board_id, NULL, h.target_comment_id, NULL, NULL, h.previous_body, h.previous_body_html,
       h.created_utc, h.created_utc + 7776000
FROM public.content_edit_history h
JOIN public.comments c ON c.id = h.target_comment_id AND c.purged_utc = 0
WHERE h.action IN ('delete', 'guild_hide', 'author_hide') AND h.target_comment_id > 0
  AND (h.previous_body IS NOT NULL OR h.previous_body_html IS NOT NULL)
  AND h.created_utc > extract(epoch from now())::bigint - 7776000;

UPDATE public.content_edit_history
SET previous_title = NULL, previous_url = NULL, previous_body = NULL, previous_body_html = NULL
WHERE action IN ('delete', 'guild_hide', 'author_hide')
  AND (previous_title IS NOT NULL OR previous_url IS NOT NULL OR previous_body IS NOT NULL OR previous_body_html IS NOT NULL);
