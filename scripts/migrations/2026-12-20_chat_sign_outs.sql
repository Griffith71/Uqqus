-- Durable chat sign-outs (helpers/chat_signouts.py, chat plan phase 2 step 2).
--
-- chat_site_devices: the chat devices the site made for an account (routes/chat.py chat_token, helpers/chat_tokens.py), one row per
-- device, kept for the account's life (account_cleanup deletes them when it closes). `sign_in` is a SHA-256 of the cookie's
-- session_id that last started it, never the id, `created_ms` is compared with a sign-out's `spare_after_ms`, a revoked row is never
-- served again. `refresh_sealed` and `mint_key` are filled from build step 3 on.
--
-- chat_device_signouts: a queue of Synapse device sign-outs, written in the same transaction as the credential change that asked
-- for them and worked after the commit, retried for ever (helpers/chat_signouts.py run_one). Kept 30 days after it is done
-- (helpers/retention.py). Never deleted with an account: a pending sign-out must still finish.
--
-- No foreign keys to users (the project's rule). Safe to run twice.
--   PYTHONPATH=. python scripts/migrate.py --apply
CREATE TABLE IF NOT EXISTS chat_site_devices (
    user_id integer NOT NULL,
    device_id character varying(64) NOT NULL,
    kind character varying(8) NOT NULL CHECK (kind IN ('chat', 'call')),
    sign_in character(64),
    created_ms bigint NOT NULL,
    last_used_utc bigint NOT NULL,
    last_token_utc bigint DEFAULT 0 NOT NULL,
    refresh_sealed text,
    revoked_utc bigint DEFAULT 0 NOT NULL,
    revoked_reason character varying(24),
    mint_key character(32),
    PRIMARY KEY (user_id, device_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_site_devices_mint ON chat_site_devices (user_id, mint_key) WHERE mint_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS chat_device_signouts (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    mxid character varying(255) NOT NULL,
    scope character varying(8) NOT NULL CHECK (scope IN ('all', 'listed')),
    device_ids text,
    spared text,
    spare_after_ms bigint NOT NULL,
    reason character varying(24) NOT NULL,
    created_utc bigint NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_try_utc bigint NOT NULL,
    done_utc bigint DEFAULT 0 NOT NULL,
    last_error character varying(64)
);
CREATE INDEX IF NOT EXISTS chat_device_signouts_due ON chat_device_signouts (next_try_utc) WHERE done_utc = 0;
CREATE INDEX IF NOT EXISTS chat_device_signouts_user ON chat_device_signouts (user_id) WHERE done_utc = 0;
