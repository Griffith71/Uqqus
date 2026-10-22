-- Bug hunt 2026-10, H3: a refund or chargeback of a coin order never reached the account (helpers/paypal_events.py).
-- The webhook now finds the order by its capture, and handles each event and each cent of an order once:
--   paypal_txns.paypal_capture_id  the capture made when the order was paid (PayPal names it in refunds)
--   paypal_txns.refunded_cents     what has gone back so far, never more than the order cost in all
--   paypal_events                  every webhook event handled, by PayPal's own event id (PayPal sends one again
--                                  when it isn't sure it arrived)
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-24_paypal_reversals.sql

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
);

ALTER TABLE public.paypal_txns ADD COLUMN IF NOT EXISTS paypal_capture_id character varying(64);
ALTER TABLE public.paypal_txns ADD COLUMN IF NOT EXISTS refunded_cents integer DEFAULT 0 NOT NULL;
CREATE INDEX IF NOT EXISTS paypal_txns_capture_idx ON public.paypal_txns (paypal_capture_id);

CREATE TABLE IF NOT EXISTS public.paypal_events (
    event_id character varying(64) PRIMARY KEY,
    event_type character varying(64) NOT NULL,
    txn_id integer,
    created_utc integer NOT NULL
);

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-11-24_paypal_reversals.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
