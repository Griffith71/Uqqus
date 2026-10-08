-- Stories (helpers/stories.py): a picture, a text card or (on a public story) a video that lasts 24 hours, shown as a ring
-- around the avatar, plus Highlights kept under the bio. Who may see a story is the same rule as for a post
-- (helpers/circles.py: Public, Subscribers or Close Friends).
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_stories.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.
-- (No foreign keys to users: users.id has no unique key in existing databases; the other newer tables skip them too.)

CREATE TABLE IF NOT EXISTS stories (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    kind character varying(8) NOT NULL,
    body character varying(300) DEFAULT '' NOT NULL,
    background character varying(16) DEFAULT '' NOT NULL,
    video_ref character varying(16) DEFAULT '' NOT NULL,
    audience smallint DEFAULT 0 NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    created_utc integer NOT NULL,
    expires_utc integer NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT stories_kind CHECK (kind IN ('image', 'text', 'video')),
    CONSTRAINT stories_audience CHECK (audience IN (0, 1, 2))
);
CREATE INDEX IF NOT EXISTS stories_user_idx ON stories (user_id, created_utc DESC);
CREATE INDEX IF NOT EXISTS stories_live_idx ON stories (expires_utc) WHERE deleted_utc = 0;

CREATE TABLE IF NOT EXISTS story_views (
    id SERIAL PRIMARY KEY,
    story_id integer NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    viewer_id integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT story_views_once UNIQUE (story_id, viewer_id)
);
CREATE INDEX IF NOT EXISTS story_views_viewer_idx ON story_views (viewer_id);

CREATE TABLE IF NOT EXISTS highlights (
    id SERIAL PRIMARY KEY,
    user_id integer NOT NULL,
    title character varying(30) NOT NULL,
    position integer DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL
);
CREATE INDEX IF NOT EXISTS highlights_user_idx ON highlights (user_id, position);

CREATE TABLE IF NOT EXISTS highlight_stories (
    highlight_id integer NOT NULL REFERENCES highlights(id) ON DELETE CASCADE,
    story_id integer NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    position integer DEFAULT 0 NOT NULL,
    CONSTRAINT highlight_stories_key PRIMARY KEY (highlight_id, story_id)
);

CREATE TABLE IF NOT EXISTS story_reports (
    id SERIAL PRIMARY KEY,
    story_id integer NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    reporter_id integer NOT NULL,
    reason character varying(200) DEFAULT '' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    resolved_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT story_reports_once UNIQUE (story_id, reporter_id)
);

ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS story_id integer;
CREATE INDEX IF NOT EXISTS media_assets_story_idx ON media_assets (story_id) WHERE story_id IS NOT NULL;
