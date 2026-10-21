-- Two-factor backup codes (helpers/mfa_backup.py, helpers/mfa_backup_store.py): ten one-time codes an account makes while
-- two-factor sign-in is on, for the day the authenticator is lost. Only a salted SHA-256 of each code is kept; a used
-- code stays as a row (`used_utc`) so the account still counts as having codes. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-15_mfa_backup_codes.sql
CREATE TABLE IF NOT EXISTS public.mfa_backup_codes (
    user_id integer NOT NULL,
    code_hash character(64) NOT NULL,
    salt character varying(32) NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    used_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (user_id, code_hash)
);
