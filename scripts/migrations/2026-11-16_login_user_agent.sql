-- Sign-in history (helpers/sign_in_history.py): the browser a sign-in came from, cut to 200 characters
-- (helpers/user_agents.py). Erased after 90 days with the address (helpers/retention.py). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-16_login_user_agent.sql
ALTER TABLE public.login_events ADD COLUMN IF NOT EXISTS user_agent character varying(200);
