-- Drafts and scheduled posts (Save draft / Schedule on Create a post).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-06_post_drafts.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS post_drafts (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    title character varying(300) DEFAULT '' NOT NULL,
    url character varying(2048) DEFAULT '' NOT NULL,
    body text DEFAULT '' NOT NULL,
    -- JSON list of guild names, and a JSON object of the options (comment_permission,
    -- paid_partnership, made_with_ai, sensitive)
    forward_guilds text DEFAULT '[]' NOT NULL,
    options text DEFAULT '{}' NOT NULL,
    -- draft | scheduled | publishing | published | failed
    status character varying(12) DEFAULT 'draft' NOT NULL,
    publish_utc integer,
    attempts smallint DEFAULT 0 NOT NULL,
    claimed_utc integer,
    error character varying(512) DEFAULT '' NOT NULL,
    published_post_id integer,
    creation_ip character varying(64) DEFAULT '' NOT NULL,
    creation_region character varying(2),
    created_utc integer DEFAULT 0 NOT NULL,
    updated_utc integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS post_drafts_user_id_index ON post_drafts USING btree (user_id);
CREATE INDEX IF NOT EXISTS post_drafts_due_index ON post_drafts USING btree (publish_utc) WHERE status = 'scheduled';
