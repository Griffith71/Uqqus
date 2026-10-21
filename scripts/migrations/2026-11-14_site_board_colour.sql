-- The site's internal boards, general (1) and systemprofile (2, where every profile post lives), still held Ruqqus's
-- old default purple; their pages and stylesheets are the site's own colour (routes/boards.py INTERNAL_BOARDS), and
-- this keeps the stored value in step. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-14_site_board_colour.sql
UPDATE public.boards SET color = '148fd5' WHERE id IN (1, 2) AND color = '805ad5';
