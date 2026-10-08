-- Polls (helpers/polls.py, helpers/poll_store.py): a poll belongs to a PRIMARY post, and every
-- forwarded copy and repost of that post shows and votes the same poll. 2 to 4 options, one vote
-- per member (no changing), closes at `closes_utc`. Nothing here records who voted what except
-- the vote row, which is never shown to anyone.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-09_polls.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS polls (
    id SERIAL PRIMARY KEY,
    post_id integer NOT NULL,
    closes_utc integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT polls_post_key UNIQUE (post_id)
);

CREATE TABLE IF NOT EXISTS poll_options (
    id SERIAL PRIMARY KEY,
    poll_id integer NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    ordinal smallint NOT NULL,
    label character varying(40) NOT NULL,
    CONSTRAINT poll_options_ordinal_key UNIQUE (poll_id, ordinal)
);

CREATE TABLE IF NOT EXISTS poll_votes (
    id SERIAL PRIMARY KEY,
    poll_id integer NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    option_id integer NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT poll_votes_one_per_member UNIQUE (poll_id, user_id)
);

CREATE INDEX IF NOT EXISTS poll_votes_option_index ON poll_votes (option_id);
