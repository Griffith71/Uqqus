-- The creator fund (helpers/creator_fund.py): a monthly budget of coins shared by the members whose posts and comments
-- other people valued most. A member takes part from the moment they opt in (creator_fund_opt_in_utc, 0 = not).
-- A month is paid once (creator_fund_months.month is unique) and a member once per month ((month, user_id) is unique);
-- the payout rows are the record of earned coins. No foreign keys to users, as the chat tables. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-12_creator_fund.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS creator_fund_opt_in_utc integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS public.creator_fund_months (
    id SERIAL PRIMARY KEY,
    month character(7) NOT NULL,
    budget_coins integer NOT NULL,
    total_points integer NOT NULL,
    paid_coins integer NOT NULL,
    created_utc integer NOT NULL,
    paid_by_id integer NOT NULL,
    CONSTRAINT creator_fund_months_month_key UNIQUE (month)
);

CREATE TABLE IF NOT EXISTS public.creator_fund_payouts (
    id SERIAL PRIMARY KEY,
    month character(7) NOT NULL,
    user_id integer NOT NULL,
    points integer NOT NULL,
    coins integer NOT NULL,
    status character varying(8) NOT NULL,
    created_utc integer NOT NULL,
    paid_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT creator_fund_payouts_pair_key UNIQUE (month, user_id),
    CONSTRAINT creator_fund_payouts_status_check CHECK (status IN ('paid', 'held'))
);

CREATE INDEX IF NOT EXISTS creator_fund_payouts_user_idx ON public.creator_fund_payouts USING btree (user_id, month DESC);
