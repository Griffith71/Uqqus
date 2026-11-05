-- Age assurance (helpers/age_declaration.py, helpers/age_assurance.py; the owner, 2026-11-05): a child state can be lifted.
-- `age_lock` NULL = no lock; 1 = a standalone locked account (the under-18 option, or an account made in a strict country: the UK, EU/EEA or
-- Australia) that a confirmed age may lift; 2 = a sub-account a parent made (Stage 2: lifted only by de-linking). `age_assured_utc` is when a
-- vendor (or an admin) confirmed 18 or over; 0 = never. We keep only that a check happened, never a birth date or an ID.
-- A lock only ever sits on a child account (age_declared 2), which already stays on the Child word filter (users_age_declared_rule), so the
-- same statement that lifts a lock moves the account to 1. Every existing child account becomes liftable. Safe to run twice.
--   PYTHONPATH=. python scripts/migrate.py --apply
ALTER TABLE users ADD COLUMN IF NOT EXISTS age_lock smallint;
ALTER TABLE users ADD COLUMN IF NOT EXISTS age_assured_utc bigint DEFAULT 0 NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS age_under_utc bigint DEFAULT 0 NOT NULL;

UPDATE users SET age_lock = 1 WHERE age_declared = 2 AND age_lock IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_age_lock_rule') THEN
        ALTER TABLE users ADD CONSTRAINT users_age_lock_rule CHECK (
            age_lock IS NULL OR (age_lock IN (1, 2) AND age_declared = 2));
    END IF;
END $$;
