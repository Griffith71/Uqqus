-- An account may keep its profile posts out of All (helpers/all_feed.py; the owner, 2026-10-27), as a guild already can
-- (boards.all_opt_out). Off for every account: nothing changes until a member switches it on in Settings > Content.
-- Its anonymous posts and the guild copies of its posts stay in All whatever it says. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-05_account_all_opt_out.sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS all_opt_out boolean DEFAULT false NOT NULL;

-- the "accounts out of All" subquery All runs is over a handful of rows
CREATE INDEX IF NOT EXISTS users_all_opt_out_idx ON users (id) WHERE all_opt_out;
