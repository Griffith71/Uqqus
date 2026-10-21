-- Bug hunt 2026-10, C1: the mod log draws its notes as HTML, and a guild's "hide" reason (and an admin nuke's ban
-- reason) was stored as typed, so markup in it ran for every reader of the log. New notes are escaped when written;
-- this escapes the ones already stored, once (it runs only while this file isn't recorded, so running it again
-- never escapes twice). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-22_escape_mod_log_notes.sql

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
);

UPDATE public.modactions
   SET _note = left(replace(replace(replace(replace(replace(_note, '&', '&amp;'), '<', '&lt;'), '>', '&gt;'), '"', '&quot;'),
                            '''', '&#x27;'), 256)
 WHERE _note IS NOT NULL
   AND (kind IN ('hide_post_from_guild', 'hide_comment_from_guild')
        OR (kind IN ('ban_post', 'ban_comment') AND _note LIKE 'admin action | %'))
   AND NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE name = '2026-11-22_escape_mod_log_notes.sql');

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-11-22_escape_mod_log_notes.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
