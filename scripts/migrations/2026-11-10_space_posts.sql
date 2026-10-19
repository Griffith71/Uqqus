-- A Space's post takes no comments: its discussion is the Space's own (helpers/comment_permission.py SPACE = 5). Spaces
-- made before this get the mark on their post and every guild copy of it. Existing comments stay where they are.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-10_space_posts.sql
UPDATE public.submissions SET comment_permission = 5
WHERE comment_permission <> 5
  AND (id IN (SELECT post_id FROM public.spaces) OR repost_id IN (SELECT post_id FROM public.spaces));
