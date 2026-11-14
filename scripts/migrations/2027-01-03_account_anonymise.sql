-- The Log, slice D3c: what an account wrote becomes anonymous when the account is deleted, by a job that is queued with the deletion, runs its first
-- batch at once and finishes in the background (helpers/account_anonymise.py). One row per account; no foreign keys (the project's rule). Safe to run twice.
CREATE TABLE IF NOT EXISTS public.account_anonymise_jobs (
    user_id integer PRIMARY KEY,
    queued_utc bigint DEFAULT 0 NOT NULL,
    updated_utc bigint DEFAULT 0 NOT NULL,
    done_utc bigint DEFAULT 0 NOT NULL,
    tries integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS account_anonymise_jobs_pending_idx ON public.account_anonymise_jobs (queued_utc) WHERE done_utc = 0;
