-- The Log, slice D4 (the audit's M6, M7, M8): the order of the public Log, indexes for the pages about one item or account, and the word filter's
-- rating of the Log's own words (a reason, an edit summary), so a reader's filter level hides them like any member text.
-- Safe to run twice.

-- M8: the rating of a reason (log_entries) and of an edit's reason and summary together (content_edit_history); 0 clean, 1 profanity, 2 extreme.
-- Written when the text is (helpers/action_log.py `rate`, a before-insert hook on the history model) and again by the word filter's re-scan.
ALTER TABLE log_entries ADD COLUMN IF NOT EXISTS reason_severity smallint DEFAULT 0 NOT NULL;
ALTER TABLE content_edit_history ADD COLUMN IF NOT EXISTS text_severity smallint DEFAULT 0 NOT NULL;

-- M7: the account's and the item's pages read entries by the item they name. A partial index each: most entries name neither.
CREATE INDEX IF NOT EXISTS log_entries_post_idx ON log_entries (target_submission_id) WHERE target_submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS log_entries_comment_idx ON log_entries (target_comment_id) WHERE target_comment_id IS NOT NULL;

-- M6: the public Log is ordered by (created_utc, id): a backfill run after live entries exist gives old entries higher ids. The earlier
-- single-column index stays (an older migration creates it, and every file must be safe to run again without changing the schema).
CREATE INDEX IF NOT EXISTS log_entries_order_idx ON log_entries (created_utc DESC, id DESC);
