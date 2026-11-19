-- Automatic checks quarantine, never ban (the owner, 2026-11-17 and 18; helpers/auto_quarantine.py). The signup guard's hold (`users.quarantined_utc`,
-- helpers/quarantine.py) is now also what the site's own checks do to an account (the spam guard, a banned link, the SEO-spam check, a picture the
-- image check matched, ban-evasion detection): the account can read the site and change its own settings but acts on no one, until an admin looks.
-- `quarantine_kind` says which: 0 is the signup guard's hold (or none, while `quarantined_utc` is 0), 1 an automatic check's. A kind 1 hold is released
-- ONLY by an admin or a successful appeal, never by verifying an email and never by time; an account the signup guard held that then trips an automatic
-- check becomes kind 1. The Log's new actions (`quarantine_user`, `unquarantine_user`: staff only; `automatic_quarantine`, `automatic_quarantine_lifted`:
-- public, naming no account) need no schema change: `log_entries.action` is free text. Safe to run twice (the column and the constraint are two statements).
--   PYTHONPATH=. python scripts/migrate.py --apply
ALTER TABLE users ADD COLUMN IF NOT EXISTS quarantine_kind smallint DEFAULT 0 NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_quarantine_kind_rule' AND conrelid = 'public.users'::regclass) THEN
        ALTER TABLE users ADD CONSTRAINT users_quarantine_kind_rule CHECK (quarantine_kind IN (0, 1));
    END IF;
END $$;
