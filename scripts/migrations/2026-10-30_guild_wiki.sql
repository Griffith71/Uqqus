-- Guild wikis and pinned resources (helpers/guild_wiki.py, helpers/guild_wiki_store.py): a guild's wiki pages, every
-- revision of them (kept: history and restore), the buttons under its header, and whether its wiki is on. Safe to run
-- twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-30_guild_wiki.sql
CREATE TABLE IF NOT EXISTS public.guild_wiki_pages (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    slug character varying(40) NOT NULL,
    title character varying(80) NOT NULL,
    edit_level character varying(12) DEFAULT 'mods' NOT NULL,
    current_revision_id integer DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    locked boolean DEFAULT false NOT NULL,
    CONSTRAINT guild_wiki_pages_level CHECK (edit_level IN ('mods', 'approved', 'members'))
);

CREATE UNIQUE INDEX IF NOT EXISTS guild_wiki_pages_slug_key ON public.guild_wiki_pages (board_id, slug);

CREATE TABLE IF NOT EXISTS public.guild_wiki_revisions (
    id SERIAL PRIMARY KEY,
    page_id integer NOT NULL REFERENCES public.guild_wiki_pages(id) ON DELETE CASCADE,
    editor_id integer NOT NULL,
    body text DEFAULT '' NOT NULL,
    body_html text DEFAULT '' NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    reason character varying(140) DEFAULT '' NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    removed_utc integer DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS guild_wiki_revisions_page_idx ON public.guild_wiki_revisions (page_id, id);
CREATE INDEX IF NOT EXISTS guild_wiki_revisions_editor_idx ON public.guild_wiki_revisions (editor_id, created_utc);

CREATE TABLE IF NOT EXISTS public.guild_resources (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    label character varying(40) NOT NULL,
    kind character varying(8) NOT NULL,
    target character varying(300) NOT NULL,
    position integer DEFAULT 0 NOT NULL,
    CONSTRAINT guild_resources_kind CHECK (kind IN ('link', 'wiki', 'post'))
);

CREATE INDEX IF NOT EXISTS guild_resources_board_idx ON public.guild_resources (board_id, position);

ALTER TABLE public.boards ADD COLUMN IF NOT EXISTS wiki_enabled boolean DEFAULT false NOT NULL;
