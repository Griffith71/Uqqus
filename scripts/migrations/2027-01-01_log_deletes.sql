-- The Log, slice D3a: what a member deletes stays in the Log, as Anonymous (the owner's decisions 3, 4, 5, 12, 25 of 2026-11-05/06; helpers/deletion.py,
-- helpers/action_log.py, routes/log_pages.py).
--   log_entries.log_type          gains `deletion`: an author's own delete after the 5-minute grace window (a delete inside it leaves no entry).
--   log_snapshots.public          the copy's text may be drawn to readers the ENTRY's audience allows (the `delete` kind: a public item's text stays
--                                 in the Log). False for every private copy (hide, author_hide, grace, legal) and for a delete whose text is not
--                                 published (a child account's, an item a moderator had removed, one in a banned guild: a line only).
--   log_snapshots.word_severity   the word filter's rating of the copy, and
--   log_snapshots.is_sensitive    whether it was sensitive: copied in when it is kept, so a reader's filter level and the Child level hide the text
--                                 of a deleted item as they hid it when it was live (a line only for a visitor; behind the cover for a member who
--                                 chose to see sensitive content).
--   log_reports                   the Report button on a Log entry that shows a person's words: once per person per entry, a reason of up to 200
--                                 characters, never shown who. An admin purges the text (the existing purge path, `scrub_for`), redacts a reason
--                                 or dismisses it.
-- A `delete` copy has no expiry (`expires_utc` 0): the retention rule skips it, and only an admin's scrub empties it.
-- No foreign keys to users (the project's rule). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2027-01-01_log_deletes.sql
ALTER TABLE public.log_snapshots ADD COLUMN IF NOT EXISTS public boolean DEFAULT false NOT NULL;
ALTER TABLE public.log_snapshots ADD COLUMN IF NOT EXISTS word_severity smallint DEFAULT 0 NOT NULL;
ALTER TABLE public.log_snapshots ADD COLUMN IF NOT EXISTS is_sensitive boolean DEFAULT false NOT NULL;

ALTER TABLE public.log_entries DROP CONSTRAINT IF EXISTS log_entries_type;
ALTER TABLE public.log_entries ADD CONSTRAINT log_entries_type CHECK (log_type IN ('content', 'sanction', 'guild', 'suppression', 'deletion'));

CREATE INDEX IF NOT EXISTS log_entries_type_order_idx ON public.log_entries (log_type, created_utc DESC, id DESC);

CREATE TABLE IF NOT EXISTS public.log_reports (
    id BIGSERIAL PRIMARY KEY,
    entry_id bigint NOT NULL,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc bigint DEFAULT 0 NOT NULL,
    status character varying(10) DEFAULT 'open' NOT NULL,
    decided_by integer,
    decided_utc bigint DEFAULT 0 NOT NULL,
    CONSTRAINT log_reports_status CHECK (status IN ('open', 'purged', 'redacted', 'dismissed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS log_reports_once_idx ON public.log_reports (entry_id, reporter_id);
CREATE INDEX IF NOT EXISTS log_reports_open_idx ON public.log_reports (entry_id) WHERE status = 'open';
