-- Tips (helpers/tips.py): every tip is its own row with its coins, a post keeps its totals, and tips lift a post in
-- the ranked sorts (Hot and Best) but never its score or the Top sort.
-- There is no migration framework: run this once against an existing database, then work out the boosts of posts
-- tipped before (old tips count as 1 coin each: their amounts were never kept):
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-13_tips.sql
--   PYTHONPATH=. python scripts/recompute_tip_boost.py --once
-- Fresh databases get the same schema from schema.sql. Safe to run twice.

ALTER TABLE award_relationships ADD COLUMN IF NOT EXISTS coins integer NOT NULL DEFAULT 1;
ALTER TABLE award_relationships ADD COLUMN IF NOT EXISTS created_utc integer NOT NULL DEFAULT 0;
-- one row per tip, not one per person and item
ALTER TABLE award_relationships DROP CONSTRAINT IF EXISTS award_post_constraint;
ALTER TABLE award_relationships DROP CONSTRAINT IF EXISTS award_comment_constraint;
ALTER TABLE award_relationships DROP CONSTRAINT IF EXISTS award_constraint;

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS tip_coins integer NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD COLUMN IF NOT EXISTS tip_boost double precision NOT NULL DEFAULT 0;

UPDATE submissions s SET tip_coins = t.coins
FROM (SELECT submission_id, SUM(coins) AS coins FROM award_relationships WHERE submission_id IS NOT NULL GROUP BY submission_id) t
WHERE s.id = t.submission_id;

CREATE OR REPLACE FUNCTION public.rank_best(public.submissions) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT 10000000.0*CAST(($1.upvotes - $1.downvotes + $1.tip_boost + 1) AS float)/((CAST(($1.age+3600) AS FLOAT)*cast((select boards.subscriber_count from boards where boards.id=$1.board_id)+10000 as float)/1000.0)^(1.35))
      $_$;

CREATE OR REPLACE FUNCTION public.rank_hot(public.submissions) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
      SELECT 1000000.0*CAST(($1.upvotes - $1.downvotes + $1.tip_boost) AS float)/((CAST(($1.age+5000) AS FLOAT)/100.0)^(1.5))
      $_$;
