-- Two kinds of bot (helpers/bots.py). A SITE bot runs on free feeds and costs nobody anything: once approved it belongs to
-- nobody and the site's admins run it (owner_id NULL). A MEMBER bot needs its member (a bot key, an API bill): it stays
-- theirs and they manage it. Either can be proposed by a member (proposed_by_id); admins approve both. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-16_site_bots.sql

ALTER TABLE bots ADD COLUMN IF NOT EXISTS kind character varying(10) DEFAULT 'member' NOT NULL;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS proposed_by_id integer;
ALTER TABLE bots ALTER COLUMN owner_id DROP NOT NULL;
UPDATE bots SET proposed_by_id = owner_id WHERE proposed_by_id IS NULL;
-- Only when the table has no rule yet: a later migration widens it (2026-11-21_self_automation.sql adds 'self'), and
-- running this file again must never narrow it back (a database with an own-account row would refuse it).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bots_kind' AND conrelid = 'public.bots'::regclass) THEN
        ALTER TABLE bots ADD CONSTRAINT bots_kind CHECK (kind IN ('site', 'member'));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS bots_proposed_idx ON bots (proposed_by_id);
