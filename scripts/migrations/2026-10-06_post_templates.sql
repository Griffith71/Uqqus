-- Saved post templates (the Template button in the post editor).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-06_post_templates.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS post_templates (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    name character varying(60) NOT NULL,
    body text NOT NULL,
    created_utc integer DEFAULT 0
);

CREATE INDEX IF NOT EXISTS post_templates_user_id_index ON post_templates USING btree (user_id);
