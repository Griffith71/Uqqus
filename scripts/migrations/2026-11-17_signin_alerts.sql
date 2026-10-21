-- New-sign-in emails (helpers/signin_alerts.py): an account with a verified email is told when it signs in from a
-- country or browser it hasn't used in the last 90 days, at most once a day. On by default; Settings > Security turns
-- it off (with the password). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-17_signin_alerts.sql
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS signin_alerts boolean DEFAULT true NOT NULL;
