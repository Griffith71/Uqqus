-- Trending topics (helpers/trending.py): the lists scripts/compute_trending.py writes, and
-- the topics an admin hid.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-07_trending.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS trending_topics (
    id SERIAL PRIMARY KEY,
    scope character varying(48) NOT NULL,
    filter_level smallint DEFAULT 1 NOT NULL,
    rank smallint NOT NULL,
    key character varying(320) NOT NULL,
    slug character varying(64) NOT NULL,
    kind character varying(8) NOT NULL,
    label character varying(120) NOT NULL,
    score double precision DEFAULT 0 NOT NULL,
    post_count integer DEFAULT 0 NOT NULL,
    author_count integer DEFAULT 0 NOT NULL,
    post_ids text DEFAULT '[]' NOT NULL,
    computed_utc integer DEFAULT 0 NOT NULL
);

CREATE TABLE IF NOT EXISTS trending_blocked (
    id SERIAL PRIMARY KEY,
    key character varying(320) NOT NULL,
    label character varying(120) DEFAULT '' NOT NULL,
    admin_id integer,
    created_utc integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS trending_topics_list_index ON trending_topics USING btree (scope, filter_level, rank);
CREATE INDEX IF NOT EXISTS trending_topics_slug_index ON trending_topics USING btree (slug);
CREATE UNIQUE INDEX IF NOT EXISTS trending_blocked_key_index ON trending_blocked USING btree (key);
