-- Circles (helpers/circles.py): an exclusive audience for an account (and, later, a guild). People are in it as a
-- close friend the owner added (free) or as a subscriber paying coins every 30 days.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_circles.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS circles (
    id SERIAL PRIMARY KEY,
    user_id integer,
    board_id integer,
    price_coins integer DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT circles_one_owner CHECK ((user_id IS NOT NULL) <> (board_id IS NOT NULL)),
    CONSTRAINT circles_price_range CHECK (price_coins >= 0 AND price_coins <= 100)
);
CREATE UNIQUE INDEX IF NOT EXISTS circles_user_key ON circles (user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS circles_board_key ON circles (board_id) WHERE board_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS circle_members (
    id SERIAL PRIMARY KEY,
    circle_id integer NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    tier character varying(12) NOT NULL,
    status character varying(12) DEFAULT 'active' NOT NULL,
    started_utc integer DEFAULT 0 NOT NULL,
    renews_utc integer DEFAULT 0 NOT NULL,
    cancelled boolean DEFAULT false NOT NULL,
    price_coins integer DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT circle_members_pair_key UNIQUE (circle_id, user_id),
    CONSTRAINT circle_members_tier CHECK (tier IN ('friend', 'subscriber')),
    CONSTRAINT circle_members_status CHECK (status IN ('active', 'ended'))
);
CREATE INDEX IF NOT EXISTS circle_members_user_idx ON circle_members (user_id);
CREATE INDEX IF NOT EXISTS circle_members_due_idx ON circle_members (renews_utc) WHERE tier = 'subscriber' AND status = 'active';

CREATE TABLE IF NOT EXISTS circle_payments (
    id SERIAL PRIMARY KEY,
    circle_id integer NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    payer_id integer NOT NULL,
    owner_id integer NOT NULL,
    coins integer NOT NULL,
    kind character varying(12) NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS circle_payments_owner_idx ON circle_payments (owner_id, created_utc DESC);
