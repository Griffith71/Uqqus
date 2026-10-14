-- Guild rules (classes/rules.py, routes/boards.py mod_add_rule / mod_edit_rule) were queried but their table was
-- created nowhere, so a fresh database failed on the first guild rule. And the guild-limit network check
-- (helpers/guild_limits.py) looks up login_events by address, which had no index.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_rules_and_login_ip.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

CREATE TABLE IF NOT EXISTS rules (
    id bigserial PRIMARY KEY,
    board_id integer REFERENCES boards(id) ON DELETE CASCADE,
    rule_body character varying(256),
    rule_html character varying,
    created_utc bigint DEFAULT 0,
    edited_utc bigint DEFAULT 0
);

CREATE INDEX IF NOT EXISTS rules_board_idx ON rules (board_id);

CREATE INDEX IF NOT EXISTS login_events_ip_idx ON login_events (ip, created_utc DESC);
