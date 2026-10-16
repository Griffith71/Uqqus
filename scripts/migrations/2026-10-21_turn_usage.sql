-- The call relay's monthly count (helpers/turn_usage.py): what coturn sent this month, against TURN_MONTHLY_GB.
-- scripts/turn_usage.py adds to it every 5 minutes from coturn's own counters; last_counter is coturn's total
-- at the last reading (it starts again from 0 when coturn restarts). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-21_turn_usage.sql
CREATE TABLE IF NOT EXISTS public.turn_usage (
    month character(7) PRIMARY KEY,
    sent_bytes bigint DEFAULT 0 NOT NULL,
    last_counter bigint,
    updated_utc integer DEFAULT 0 NOT NULL
);
