-- Bug hunt 2026-10: deleting an account left the grants it gave apps (client_auths), and a token signs an app in
-- whatever the account's state, so an app kept posting, voting and editing as a deleted account, and could refresh its
-- token for ever. Deleting an account now removes them (helpers/account_cleanup.py); this removes the ones left by
-- accounts deleted before. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-25_deleted_accounts_app_grants.sql

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
);

DELETE FROM public.client_auths WHERE user_id IN (SELECT id FROM public.users WHERE is_deleted = true);

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-11-25_deleted_accounts_app_grants.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
