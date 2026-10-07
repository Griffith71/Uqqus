-- Community note requests (helpers/community_notes.py): what members asked a note for from the
-- flag menu, and the notes admins wrote. A request is not a flag (flags are policy reports).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-08_community_notes.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS note_requests (
    id SERIAL PRIMARY KEY,
    post_id integer,
    comment_id integer,
    user_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT note_requests_one_target CHECK ((post_id IS NULL) <> (comment_id IS NULL))
);

CREATE TABLE IF NOT EXISTS community_notes (
    id SERIAL PRIMARY KEY,
    post_id integer,
    comment_id integer,
    body character varying(600) NOT NULL,
    admin_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    removed_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT community_notes_one_target CHECK ((post_id IS NULL) <> (comment_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS note_requests_post_user_index ON note_requests USING btree (user_id, post_id) WHERE post_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS note_requests_comment_user_index ON note_requests USING btree (user_id, comment_id) WHERE comment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS note_requests_post_index ON note_requests USING btree (post_id) WHERE post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS note_requests_comment_index ON note_requests USING btree (comment_id) WHERE comment_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS community_notes_post_live_index ON community_notes USING btree (post_id) WHERE post_id IS NOT NULL AND removed_utc = 0;
CREATE UNIQUE INDEX IF NOT EXISTS community_notes_comment_live_index ON community_notes USING btree (comment_id) WHERE comment_id IS NOT NULL AND removed_utc = 0;
