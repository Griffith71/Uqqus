-- Bug hunt 2026-10, H4: users.negative_balance_cents is what an account owes (never below 0), and every check reads it
-- as "owes something" when it isn't 0. A purchase of 0 coins left it at -49, which locked the account for good
-- (votes, tips, buying coins, Premium). The routes no longer allow that; this sets every value below 0 back to 0,
-- once, saying how many accounts it changed (it runs only while this file isn't recorded), and then keeps the
-- column from ever going below 0 again. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-23_owed_never_below_zero.sql

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
);

DO $$
DECLARE
    changed integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE name = '2026-11-23_owed_never_below_zero.sql') THEN
        UPDATE public.users SET negative_balance_cents = 0 WHERE negative_balance_cents < 0;
        GET DIAGNOSTICS changed = ROW_COUNT;
        RAISE NOTICE 'negative_balance_cents: % account(s) below 0 set back to 0', changed;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'users_owed_never_below_zero' AND conrelid = 'public.users'::regclass) THEN
        ALTER TABLE public.users ADD CONSTRAINT users_owed_never_below_zero CHECK (negative_balance_cents >= 0);
    END IF;
END $$;

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-11-23_owed_never_below_zero.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
