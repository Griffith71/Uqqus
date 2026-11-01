-- QR sign-in, its groundwork (helpers/qr_login.py, helpers/sign_in_history.py). `users.qr_login_off`: the member switched
-- it off (Settings > Security: off needs nothing, back on needs the password and tells them). `login_events.method`: how a
-- sign-in was made, 'password' or 'qr'; NULL on rows from before this, when it wasn't recorded. It stays as long as the
-- row does (helpers/retention.py: about 14 months), since it says nothing about where the person was. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-13_qr_login.sql
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS qr_login_off boolean DEFAULT false NOT NULL;
ALTER TABLE public.login_events ADD COLUMN IF NOT EXISTS method character varying(16);
