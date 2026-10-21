-- Guild analytics (helpers/guild_insights.py, helpers/guild_insights_store.py): one row per guild and day of three
-- counts: people who joined, people who left, and times the guild's page was opened. Counts only, no who; pruned after
-- 400 days (helpers/retention.py). No foreign keys. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-20_guild_day_stats.sql
CREATE TABLE IF NOT EXISTS public.guild_day_stats (
    board_id integer NOT NULL,
    day integer NOT NULL,
    joins integer DEFAULT 0 NOT NULL,
    leaves integer DEFAULT 0 NOT NULL,
    page_views integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (board_id, day)
);
