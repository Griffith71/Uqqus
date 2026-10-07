-- Co-authored posts (helpers/coauthors.py): the accounts invited to share a post, and whether they
-- accepted. A post stays one post; this only says whose name it carries and where it is listed.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-08_coauthors.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS post_coauthors (
    id SERIAL PRIMARY KEY,
    post_id integer NOT NULL,
    user_id integer NOT NULL,
    invited_by_id integer NOT NULL,
    status character varying(8) DEFAULT 'pending' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    accepted_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT post_coauthors_pair_key UNIQUE (post_id, user_id),
    CONSTRAINT post_coauthors_status CHECK (status IN ('pending', 'accepted'))
);

CREATE INDEX IF NOT EXISTS post_coauthors_user_index ON post_coauthors USING btree (user_id, status);
