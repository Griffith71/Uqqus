-- Account search finds a handle OR a display name (routes/search.py, the @ search): `ILIKE '%word%'` on `users.username`
-- and `users.display_name`. `users.username` already has a trigram index; this adds the one for display names, so
-- Postgres can answer it without reading every account.
-- CONCURRENTLY builds an index without locking out sign-ups and cannot run inside BEGIN ... COMMIT, so run this
-- file outside a transaction (scripts/migrate.py sends such files a statement at a time, as psql -f does). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-18_user_name_search_index.sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS users_display_name_trgm_idx ON public.users USING gin (display_name gin_trgm_ops);
