-- Search (helpers/search_query.py) finds words in a post's text as well as its title. The title and the link
-- already have trigram indexes; this one lets Postgres answer `body ILIKE '%word%'` without reading every post.
-- There is no migration framework: run this once against an existing database, OUTSIDE a transaction
-- (CONCURRENTLY builds the index without locking out new posts, and cannot run inside BEGIN ... COMMIT):
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-11_search_body_index.sql
-- Fresh databases get the same index from schema.sql. Safe to run twice.

CREATE INDEX CONCURRENTLY IF NOT EXISTS submissions_body_trgm_idx ON submissions_aux USING gin (body gin_trgm_ops);
