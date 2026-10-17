-- Shared drafts keep the post options a regular post has (a link, who can comment, content disclosure, sensitive,
-- guilds to forward to) as one small JSON text, written by any author and used when the draft is published
-- (helpers/collab_rules.py clean_options, routes/collab.py). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-03_collab_options.sql
ALTER TABLE public.collab_docs ADD COLUMN IF NOT EXISTS options text DEFAULT '{}' NOT NULL;
