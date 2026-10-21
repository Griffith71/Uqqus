-- A third kind of bot (helpers/bots.py SELF): automated posting on a member's OWN account, which they keep using as
-- themselves. Its row's user_id is the member's account (owner_id and proposed_by_id are the same account); nothing
-- that deletes or deactivates a bot account touches it. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-21_self_automation.sql

ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_kind;
ALTER TABLE bots ADD CONSTRAINT bots_kind CHECK (kind IN ('site', 'member', 'self'));
