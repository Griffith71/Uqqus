-- The site's colour is now VS Code's Light Blue (#23a9f2, the SITE_COLOR default in ruqqus/__main__.py; it was #148fd5).
-- A guild created while the default was #148fd5 stored it, and a guild page draws its own colour, so those guilds
-- follow the site: they take the new default (color_nonce moves so the cached guild stylesheets are fetched again).
-- #2aabee (Telegram's blue) and #0085d0 (VS Code's Blue) were the default for a day each during development and are
-- taken the same way.
-- A site that sets SITE_COLOR to its own colour should record this file without running it (migrate.py --baseline),
-- or its guilds stored as the old default would move away from it. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-06_brand_blue_default.sql
UPDATE public.boards
   SET color = '23a9f2', color_nonce = COALESCE(color_nonce, 0) + 1
 WHERE lower(color) IN ('148fd5', '2aabee', '0085d0');
