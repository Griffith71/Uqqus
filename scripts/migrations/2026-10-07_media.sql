-- Media stored in a member's own linked account (helpers/media): the accounts members
-- linked, and one row per uploaded file. The files themselves are never stored here.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-07_media.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS media_accounts (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    provider character varying(16) NOT NULL,
    external_id character varying(128) DEFAULT '' NOT NULL,
    scopes text DEFAULT '' NOT NULL,
    refresh_token_encrypted text DEFAULT '' NOT NULL,
    status character varying(12) DEFAULT 'active' NOT NULL,
    settings text DEFAULT '{}' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    updated_utc integer DEFAULT 0 NOT NULL
);

CREATE TABLE IF NOT EXISTS media_assets (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    account_id integer NOT NULL,
    provider character varying(16) NOT NULL,
    kind character varying(8) NOT NULL,
    provider_ref character varying(255) DEFAULT '' NOT NULL,
    token character varying(64) NOT NULL,
    ext character varying(8) DEFAULT '' NOT NULL,
    size bigint DEFAULT 0 NOT NULL,
    checksum character varying(128) DEFAULT '' NOT NULL,
    width integer,
    height integer,
    status character varying(12) DEFAULT 'pending' NOT NULL,
    submission_id integer,
    comment_id integer,
    created_utc integer DEFAULT 0 NOT NULL,
    updated_utc integer DEFAULT 0 NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS media_accounts_user_provider_index ON media_accounts USING btree (user_id, provider);
CREATE INDEX IF NOT EXISTS media_assets_user_id_index ON media_assets USING btree (user_id, created_utc);
CREATE INDEX IF NOT EXISTS media_assets_submission_id_index ON media_assets USING btree (submission_id) WHERE submission_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS media_assets_comment_id_index ON media_assets USING btree (comment_id) WHERE comment_id IS NOT NULL;
