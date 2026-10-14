-- Bot accounts (helpers/bots.py rules, helpers/bot_store.py SQL): a member applies for a bot, an admin approves it,
-- and the site posts to it from the feeds it was approved with (scripts/run_bots.py) or the owner's own script posts
-- through a bot key (an OAuth grant for the bot account). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-15_bots.sql

-- An account that is a bot. Nobody logs into it (its password hash never verifies and login refuses it); the member who
-- runs it is bot_owner_id. The profile says "Automated by @owner".
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot boolean DEFAULT false NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS bot_owner_id integer;
CREATE INDEX IF NOT EXISTS users_bot_owner_idx ON users (bot_owner_id) WHERE bot_owner_id IS NOT NULL;

-- One row per bot: its application and its state. The username is kept here too, so a rejected bot (whose account is
-- removed) still shows in its owner's list with the admin's note.
CREATE TABLE IF NOT EXISTS bots (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    owner_id integer NOT NULL,
    username character varying(255) NOT NULL,
    status character varying(10) DEFAULT 'pending' NOT NULL,
    purpose character varying(500) DEFAULT '' NOT NULL,
    forward_guilds text DEFAULT '[]' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    reviewed_by_id integer,
    reviewed_utc integer DEFAULT 0 NOT NULL,
    review_note character varying(500) DEFAULT '' NOT NULL,
    paused_reason character varying(300) DEFAULT '' NOT NULL,
    paused_by_admin boolean DEFAULT false NOT NULL,
    day integer DEFAULT 0 NOT NULL,
    posts_today integer DEFAULT 0 NOT NULL,
    CONSTRAINT bots_one_per_account UNIQUE (user_id),
    CONSTRAINT bots_status CHECK (status IN ('pending', 'approved', 'rejected', 'paused'))
);
CREATE INDEX IF NOT EXISTS bots_owner_idx ON bots (owner_id);
CREATE INDEX IF NOT EXISTS bots_pending_idx ON bots (created_utc) WHERE status = 'pending';

-- Where a bot's posts come from. A source added after the bot was approved waits for an admin (status 'pending').
CREATE TABLE IF NOT EXISTS bot_sources (
    id SERIAL PRIMARY KEY,
    bot_id integer NOT NULL,
    kind character varying(10) DEFAULT 'feed' NOT NULL,
    url character varying(300) NOT NULL,
    title character varying(200) DEFAULT '' NOT NULL,
    status character varying(10) DEFAULT 'pending' NOT NULL,
    added_utc integer DEFAULT 0 NOT NULL,
    approved_by_id integer,
    approved_utc integer DEFAULT 0 NOT NULL,
    check_minutes integer DEFAULT 30 NOT NULL,
    last_checked_utc integer DEFAULT 0 NOT NULL,
    next_check_utc integer DEFAULT 0 NOT NULL,
    etag character varying(300) DEFAULT '' NOT NULL,
    last_modified character varying(100) DEFAULT '' NOT NULL,
    fail_count integer DEFAULT 0 NOT NULL,
    last_error character varying(300) DEFAULT '' NOT NULL,
    posted_count integer DEFAULT 0 NOT NULL,
    read_once boolean DEFAULT false NOT NULL,
    CONSTRAINT bot_sources_once UNIQUE (bot_id, url),
    CONSTRAINT bot_sources_status CHECK (status IN ('pending', 'approved', 'rejected', 'paused')),
    CONSTRAINT bot_sources_kind CHECK (kind IN ('feed'))
);
CREATE INDEX IF NOT EXISTS bot_sources_due_idx ON bot_sources (next_check_utc) WHERE status = 'approved';

-- Which feed items a source has already handled (posted, skipped or refused), so nothing is posted twice.
CREATE TABLE IF NOT EXISTS bot_seen (
    source_id integer NOT NULL,
    item_key character varying(64) NOT NULL,
    post_id integer,
    seen_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (source_id, item_key)
);
CREATE INDEX IF NOT EXISTS bot_seen_age_idx ON bot_seen (seen_utc);
