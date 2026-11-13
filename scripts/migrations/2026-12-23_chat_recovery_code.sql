-- Whether the member said they kept their chat recovery code (the chat plan's phase 3): set when they press "I've saved it" under a new code
-- (or "I have it" on the chat page's reminder), cleared when a new code is saved. 0 for every account before this, so those are reminded
-- once. A time, nothing else. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-23_chat_recovery_code.sql
ALTER TABLE public.chat_identities ADD COLUMN IF NOT EXISTS code_saved_utc integer DEFAULT 0 NOT NULL;
