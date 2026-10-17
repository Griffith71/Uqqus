-- Guild flair (helpers/guild_flair.py, helpers/guild_flair_store.py): a guild's list of flairs, the flair on a guild's
-- copy of a post (`submissions.guild_flair_id`: posts reach a guild only as forwards), and whether the guild requires
-- one. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-29_guild_flair.sql
CREATE TABLE IF NOT EXISTS public.guild_flairs (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    name character varying(24) NOT NULL,
    colour character varying(12) DEFAULT 'gray' NOT NULL,
    position integer DEFAULT 0 NOT NULL,
    name_severity smallint DEFAULT 0 NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS guild_flairs_name_key ON public.guild_flairs (board_id, lower(name));
CREATE INDEX IF NOT EXISTS guild_flairs_board_idx ON public.guild_flairs (board_id, position);

ALTER TABLE public.submissions ADD COLUMN IF NOT EXISTS guild_flair_id integer;
CREATE INDEX IF NOT EXISTS submissions_guild_flair_idx ON public.submissions (board_id, guild_flair_id) WHERE guild_flair_id IS NOT NULL;

ALTER TABLE public.boards ADD COLUMN IF NOT EXISTS require_flair boolean DEFAULT false NOT NULL;
