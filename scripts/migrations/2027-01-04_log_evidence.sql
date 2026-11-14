-- The Log, slice D3d: the ban reveal. When an admin bans or suspends an account they may cite the items that justify it; once a second admin approves, the
-- Log's lines about those items name the author instead of Anonymous (helpers/log_evidence.py). One row per cited item. No foreign keys (the project's
-- rule), and nothing that strips an anonymous item's pointers ever touches this table: citing an item and then deleting the account or taking your name
-- off it does not undo the reveal. Safe to run twice.
CREATE TABLE IF NOT EXISTS public.log_evidence (
    id bigserial PRIMARY KEY,
    entry_id bigint NOT NULL,
    target_user_id integer NOT NULL,
    post_id integer,
    comment_id integer,
    proposed_by integer NOT NULL,
    proposed_utc bigint DEFAULT 0 NOT NULL,
    status character varying(10) DEFAULT 'pending' NOT NULL,
    decided_by integer,
    decided_utc bigint DEFAULT 0 NOT NULL,
    sole_admin boolean DEFAULT false NOT NULL,
    withdrawn_utc bigint DEFAULT 0 NOT NULL,
    CONSTRAINT log_evidence_status CHECK (status IN ('pending', 'approved', 'declined', 'withdrawn')),
    CONSTRAINT log_evidence_one_item CHECK ((post_id IS NULL) <> (comment_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS log_evidence_post_once_idx ON public.log_evidence (entry_id, post_id) WHERE post_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS log_evidence_comment_once_idx ON public.log_evidence (entry_id, comment_id) WHERE comment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS log_evidence_pending_idx ON public.log_evidence (proposed_utc) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS log_evidence_target_idx ON public.log_evidence (target_user_id) WHERE status IN ('pending', 'approved');
CREATE INDEX IF NOT EXISTS log_evidence_post_idx ON public.log_evidence (post_id) WHERE post_id IS NOT NULL AND status = 'approved';
CREATE INDEX IF NOT EXISTS log_evidence_comment_idx ON public.log_evidence (comment_id) WHERE comment_id IS NOT NULL AND status = 'approved';
