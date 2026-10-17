-- Fixes from the audits of 2026-10-16 and 2026-11 (AUDIT_2026-10.md, AUDIT_2026-11.md). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-02_audit_fixes.sql

-- $-04: a coin buys Premium only when the member asks; renewal is opt-in. Members with a week running when this
-- ran had been renewing automatically, so they keep it on.
-- D-02: the migration ledger (scripts/migrate.py). This file records itself at the end, and the one-off UPDATE below
-- runs only while it isn't recorded, so running the file again never turns back on a renewal someone switched off.
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS premium_auto_renew boolean DEFAULT false NOT NULL;
UPDATE public.users SET premium_auto_renew = true
 WHERE premium_expires_utc > extract(epoch from now())::int AND premium_auto_renew = false
   AND NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE name = '2026-11-02_audit_fixes.sql');

-- M-08: every group add is logged, so the daily cap can't be reset by removing and adding again.
CREATE TABLE IF NOT EXISTS public.chat_group_adds (
    id SERIAL PRIMARY KEY,
    adder_id integer NOT NULL,
    group_id integer NOT NULL,
    user_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_group_adds_adder_idx ON public.chat_group_adds (adder_id, created_utc);

-- M-03 / M-04: the chat recovery key is kept only locked by the member's browser (password and recovery-code
-- copies); the server-readable copy is cleared by each member's browser once it has made them. ready_utc: when a
-- member's chat first finished setting up (messages to someone not ready wait in the sender's browser).
ALTER TABLE public.chat_identities ADD COLUMN IF NOT EXISTS key_salt character varying(64);
ALTER TABLE public.chat_identities ADD COLUMN IF NOT EXISTS key_blob_password text;
ALTER TABLE public.chat_identities ADD COLUMN IF NOT EXISTS key_blob_recovery text;
ALTER TABLE public.chat_identities ADD COLUMN IF NOT EXISTS ready_utc integer DEFAULT 0 NOT NULL;

-- G-04: wiki page titles and pinned-resource labels are rated by the word filter like other text.
ALTER TABLE public.guild_wiki_pages ADD COLUMN IF NOT EXISTS title_severity smallint DEFAULT 0 NOT NULL;
ALTER TABLE public.guild_resources ADD COLUMN IF NOT EXISTS label_severity smallint DEFAULT 0 NOT NULL;

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-11-02_audit_fixes.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
