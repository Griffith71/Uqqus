-- Muting an account (helpers/muting.py): the accounts a member muted. A mute only changes
-- what the muter sees; it is not a block (userblocks), and the muted account is not told.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-08_muting.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS usermutes (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    target_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT usermutes_pair_key UNIQUE (user_id, target_id)
);
