-- The Log (the owner, 2026-10-31; helpers/action_log.py): one append-only record of what guildmasters and admins decide,
-- in place of the guild-only `modactions` table and admin actions that wrote nothing (a ban overwrote the last one's reason).
-- Modelled on Wikipedia's Special:Log: a type, an action, who did it, to what, which rule, and a written reason.
--   log_entries    one row per decision. `audience` is the ONLY visibility rule: 0 public, 1 that guild's members and the
--                  staff, 2 staff (admin level 3), 3 level-4 admins (legal). `reason` is plain text, escaped when drawn;
--                  `staff_note` is never shown to the public. No text of a removed item is ever in an entry: that is a
--                  snapshot, below. An entry is never deleted (a redaction sets redacted_utc).
--   log_snapshots  the item as it was when it was hidden, removed or deleted by its author, kept privately (the author, the
--                  guild's guildmasters for a guild hide, admins) until expires_utc, then its text columns are emptied.
-- content_edit_history gains an optional edit summary (the per-item history stays the record of edits).
-- Existing modactions rows are copied by `PYTHONPATH=. python scripts/backfill_action_log.py --once` (idempotent: it
-- skips a row whose id is already in log_entries.legacy_modaction_id).
-- No foreign keys to users (the project's rule). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-09_action_log.sql
CREATE TABLE IF NOT EXISTS public.log_snapshots (
    id BIGSERIAL PRIMARY KEY,
    entry_id bigint,
    author_id integer,
    board_id integer,
    target_submission_id integer,
    target_comment_id integer,
    legal_hold boolean DEFAULT false NOT NULL,
    title character varying(500),
    url character varying(500),
    body character varying(25000),
    body_html character varying(50000),
    created_utc bigint DEFAULT 0 NOT NULL,
    expires_utc bigint DEFAULT 0 NOT NULL,
    scrubbed_utc bigint DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS log_snapshots_entry_idx ON public.log_snapshots (entry_id);
CREATE INDEX IF NOT EXISTS log_snapshots_post_idx ON public.log_snapshots (target_submission_id) WHERE target_submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS log_snapshots_comment_idx ON public.log_snapshots (target_comment_id) WHERE target_comment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS log_snapshots_due_idx ON public.log_snapshots (expires_utc) WHERE scrubbed_utc = 0;

CREATE TABLE IF NOT EXISTS public.log_entries (
    id BIGSERIAL PRIMARY KEY,
    created_utc bigint DEFAULT 0 NOT NULL,
    log_type character varying(12) NOT NULL,
    action character varying(32) NOT NULL,
    actor_id integer,
    actor_role character varying(12) DEFAULT 'guildmaster' NOT NULL,
    board_id integer,
    target_user_id integer,
    target_submission_id integer,
    target_comment_id integer,
    rule_key character varying(24) DEFAULT '' NOT NULL,
    reason character varying(512) DEFAULT '' NOT NULL,
    staff_note character varying(512) DEFAULT '' NOT NULL,
    audience smallint DEFAULT 0 NOT NULL,
    snapshot_id bigint,
    notice_sent boolean DEFAULT false NOT NULL,
    redacted_utc bigint DEFAULT 0 NOT NULL,
    redacted_by integer,
    legacy_modaction_id bigint,
    CONSTRAINT log_entries_type CHECK (log_type IN ('content', 'sanction', 'guild', 'suppression')),
    CONSTRAINT log_entries_role CHECK (actor_role IN ('guildmaster', 'admin', 'system')),
    CONSTRAINT log_entries_audience CHECK (audience BETWEEN 0 AND 3)
);

CREATE INDEX IF NOT EXISTS log_entries_type_idx ON public.log_entries (log_type, id DESC);
CREATE INDEX IF NOT EXISTS log_entries_board_idx ON public.log_entries (board_id, id DESC);
CREATE INDEX IF NOT EXISTS log_entries_target_user_idx ON public.log_entries (target_user_id);
CREATE INDEX IF NOT EXISTS log_entries_actor_idx ON public.log_entries (actor_id);
CREATE INDEX IF NOT EXISTS log_entries_created_idx ON public.log_entries (created_utc);
CREATE UNIQUE INDEX IF NOT EXISTS log_entries_legacy_idx ON public.log_entries (legacy_modaction_id) WHERE legacy_modaction_id IS NOT NULL;

ALTER TABLE public.content_edit_history ADD COLUMN IF NOT EXISTS summary character varying(256);
