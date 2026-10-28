-- The signup guard (helpers/signup_risk.py, helpers/quarantine.py, /admin/signups). `users.quarantined_utc` is when a
-- sign-up that looked like a bot was held: until an admin approves it or it verifies an email that isn't a throwaway,
-- the account can't act on anyone else. 0 is not held. `signup_reviews` is one row per account made since: its score,
-- the reasons (fixed keys, helpers/signup_risk.py LABELS), a keyed hash of its network (never an address) and the shape
-- of its name, so the admins can see clusters. `decision` is '' while held, then approved, verified or banned;
-- 'allowed' for a sign-up that was never held. Rows go 90 days after they were decided (helpers/retention.py).
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-04_signup_guard.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS quarantined_utc integer DEFAULT 0 NOT NULL;
CREATE INDEX IF NOT EXISTS users_quarantined_idx ON users (quarantined_utc) WHERE quarantined_utc > 0;

CREATE TABLE IF NOT EXISTS signup_reviews (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    score smallint DEFAULT 0 NOT NULL,
    reasons character varying(300) DEFAULT '' NOT NULL,
    network character varying(16) DEFAULT '' NOT NULL,
    name_shape character varying(40) DEFAULT '' NOT NULL,
    decision character varying(16) DEFAULT '' NOT NULL,
    decided_utc integer DEFAULT 0 NOT NULL,
    decided_by integer DEFAULT 0 NOT NULL,
    CONSTRAINT signup_reviews_user_key UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS signup_reviews_waiting_idx ON signup_reviews (created_utc) WHERE decision = '';
CREATE INDEX IF NOT EXISTS signup_reviews_network_idx ON signup_reviews (network, created_utc);
CREATE INDEX IF NOT EXISTS signup_reviews_shape_idx ON signup_reviews (name_shape, created_utc);
