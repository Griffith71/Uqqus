-- Push notifications (helpers/web_push.py, helpers/push_store.py): each browser a member turned them on in is a
-- subscription (its push service's address and its two keys); whether a notification names who wrote; and, per group,
-- what a member is told of (0 every message, 1 calls only, 2 nothing). No foreign keys to users. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-27_push.sql
CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    endpoint character varying(1024) NOT NULL,
    p256dh character varying(128) NOT NULL,
    auth character varying(64) NOT NULL,
    created_utc integer NOT NULL,
    last_ok_utc integer DEFAULT 0 NOT NULL,
    failures integer DEFAULT 0 NOT NULL,
    CONSTRAINT push_subscriptions_endpoint_key UNIQUE (endpoint)
);
CREATE INDEX IF NOT EXISTS push_subscriptions_user_idx ON public.push_subscriptions (user_id);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS push_names boolean DEFAULT true NOT NULL;
ALTER TABLE public.chat_group_members ADD COLUMN IF NOT EXISTS push_mode smallint DEFAULT 0 NOT NULL;
