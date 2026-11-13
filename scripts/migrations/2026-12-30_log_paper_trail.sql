-- The Log, slice D2: the paper trail made complete (the audit of 2026-11-05; the owner's decisions 1, 13 and 26; helpers/action_log.py,
-- helpers/log_appeals.py).
--   log_entries.until_utc        when a suspension ends (0: not a timed one). A lapse writes a system `unsuspend_user` entry of its own.
--   log_entries.linked_entry_id  an entry about another entry: the level-4 detail of an automatic removal, a lapse, a correction and the
--                                reversal an appeal makes. The original is never edited.
--   log_entries.actor_role       gains `author`: a post's author hiding a comment under it is a decision too, and is not a guildmaster's.
--   log_failures                 an entry the Log could not write (the action itself always stands): counted on /admin and /admin/log.
--   log_appeals                  a person's appeal against a hide, a removal, an exile, a suspension or a ban: one per person per entry,
--                                private (a reason of up to 500 characters). `board_id` is the guild whose guildmasters decide it, NULL
--                                for the site's admins. The outcome is `upheld`, `reversed` or `dismissed`; a reversal links the entry
--                                its undo wrote.
-- No foreign keys to users (the project's rule). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-30_log_paper_trail.sql
ALTER TABLE public.log_entries ADD COLUMN IF NOT EXISTS until_utc bigint DEFAULT 0 NOT NULL;
ALTER TABLE public.log_entries ADD COLUMN IF NOT EXISTS linked_entry_id bigint;

CREATE INDEX IF NOT EXISTS log_entries_linked_idx ON public.log_entries (linked_entry_id) WHERE linked_entry_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS log_entries_suspended_idx ON public.log_entries (until_utc) WHERE until_utc > 0;

ALTER TABLE public.log_entries DROP CONSTRAINT IF EXISTS log_entries_role;
ALTER TABLE public.log_entries ADD CONSTRAINT log_entries_role CHECK (actor_role IN ('guildmaster', 'admin', 'system', 'author'));

CREATE TABLE IF NOT EXISTS public.log_failures (
    id BIGSERIAL PRIMARY KEY,
    created_utc bigint DEFAULT 0 NOT NULL,
    action character varying(32) DEFAULT '' NOT NULL,
    detail character varying(300) DEFAULT '' NOT NULL
);

CREATE INDEX IF NOT EXISTS log_failures_created_idx ON public.log_failures (created_utc);

CREATE TABLE IF NOT EXISTS public.log_appeals (
    id BIGSERIAL PRIMARY KEY,
    entry_id bigint NOT NULL,
    user_id integer NOT NULL,
    board_id integer,
    reason character varying(500) DEFAULT '' NOT NULL,
    created_utc bigint DEFAULT 0 NOT NULL,
    status character varying(10) DEFAULT 'open' NOT NULL,
    decided_by integer,
    decided_utc bigint DEFAULT 0 NOT NULL,
    decision_note character varying(500) DEFAULT '' NOT NULL,
    reversal_entry_id bigint,
    CONSTRAINT log_appeals_status CHECK (status IN ('open', 'upheld', 'reversed', 'dismissed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS log_appeals_once_idx ON public.log_appeals (entry_id, user_id);
CREATE INDEX IF NOT EXISTS log_appeals_open_idx ON public.log_appeals (board_id, id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS log_appeals_user_idx ON public.log_appeals (user_id, created_utc);
