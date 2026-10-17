-- Shared drafts and collaborative posts (helpers/collab_rules.py, collab_ot.py, collab_authorship.py,
-- collab_store.py): the working copy and who typed what, every op (private to the authors), the authors, the public
-- revisions of the published post, the authors' proposals and votes, and when each account was last active (the
-- rule that leaves inactive authors uncounted). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-01_collab_posts.sql
CREATE TABLE IF NOT EXISTS public.collab_docs (
    id SERIAL PRIMARY KEY,
    creator_id integer NOT NULL,
    title text DEFAULT '' NOT NULL,
    body text DEFAULT '' NOT NULL,
    revision integer DEFAULT 0 NOT NULL,
    authorship text DEFAULT '{}' NOT NULL,
    rules text DEFAULT '{}' NOT NULL,
    post_id integer,
    published_revision integer DEFAULT 0 NOT NULL,
    forked_from_doc_id integer,
    forked_from_post_id integer,
    forked_at_publish_id integer,
    created_utc integer DEFAULT 0 NOT NULL,
    updated_utc integer DEFAULT 0 NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS collab_docs_post_key ON public.collab_docs (post_id) WHERE post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS collab_docs_forked_idx ON public.collab_docs (forked_from_post_id) WHERE forked_from_post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS collab_docs_creator_idx ON public.collab_docs (creator_id, created_utc);

CREATE TABLE IF NOT EXISTS public.collab_authors (
    doc_id integer NOT NULL REFERENCES public.collab_docs(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    joined_utc integer DEFAULT 0 NOT NULL,
    left_utc integer DEFAULT 0 NOT NULL,
    added_by_proposal_id integer,
    PRIMARY KEY (doc_id, user_id)
);

CREATE INDEX IF NOT EXISTS collab_authors_user_idx ON public.collab_authors (user_id, left_utc);

CREATE TABLE IF NOT EXISTS public.collab_ops (
    doc_id integer NOT NULL REFERENCES public.collab_docs(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    user_id integer NOT NULL,
    field character varying(5) NOT NULL,
    op text NOT NULL,
    client_key character varying(32) DEFAULT '' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (doc_id, revision),
    CONSTRAINT collab_ops_field CHECK (field IN ('title', 'body'))
);

CREATE INDEX IF NOT EXISTS collab_ops_user_idx ON public.collab_ops (user_id, created_utc);
CREATE UNIQUE INDEX IF NOT EXISTS collab_ops_client_key ON public.collab_ops (doc_id, user_id, client_key) WHERE client_key <> '';

CREATE TABLE IF NOT EXISTS public.collab_publishes (
    id SERIAL PRIMARY KEY,
    doc_id integer NOT NULL REFERENCES public.collab_docs(id) ON DELETE CASCADE,
    number integer NOT NULL,
    revision integer NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    authorship text NOT NULL,
    published_by integer NOT NULL,
    published_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT collab_publishes_number_key UNIQUE (doc_id, number)
);

CREATE TABLE IF NOT EXISTS public.collab_proposals (
    id SERIAL PRIMARY KEY,
    doc_id integer NOT NULL REFERENCES public.collab_docs(id) ON DELETE CASCADE,
    kind character varying(16) NOT NULL,
    subject_user_id integer,
    payload text DEFAULT '{}' NOT NULL,
    proposed_by integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    closes_utc integer DEFAULT 0 NOT NULL,
    decided_utc integer DEFAULT 0 NOT NULL,
    outcome character varying(10) DEFAULT 'open' NOT NULL,
    invitee_answer character varying(10) DEFAULT '' NOT NULL,
    CONSTRAINT collab_proposals_kind CHECK (kind IN ('add_author', 'remove_author', 'change_rules', 'delete_post', 'publish')),
    CONSTRAINT collab_proposals_outcome CHECK (outcome IN ('open', 'passed', 'failed', 'withdrawn', 'expired'))
);

CREATE INDEX IF NOT EXISTS collab_proposals_doc_idx ON public.collab_proposals (doc_id, outcome);
CREATE INDEX IF NOT EXISTS collab_proposals_subject_idx ON public.collab_proposals (subject_user_id, outcome) WHERE subject_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.collab_votes (
    proposal_id integer NOT NULL REFERENCES public.collab_proposals(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    vote boolean NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (proposal_id, user_id)
);

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS last_active_utc integer DEFAULT 0 NOT NULL;

-- accounts active before this column existed: their last sign-in (a later visit stamps it again)
UPDATE public.users u SET last_active_utc = l.last_login
FROM (SELECT user_id, MAX(created_utc) AS last_login FROM public.login_events GROUP BY user_id) l
WHERE l.user_id = u.id AND u.last_active_utc < l.last_login;
