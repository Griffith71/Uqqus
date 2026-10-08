-- Who can see a post (helpers/circles.py): 0 public, 1 subscribers, 2 close friends, 3 a Circle guild.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-10_circle_posts.sql
-- Fresh databases get the same schema from schema.sql. Safe to run twice.
-- (A post that is not public also has post_public = false, so every list that already asks for public posts
-- leaves it out until a query adds the Circle clause for the people who may see it.)

ALTER TABLE submissions ADD COLUMN IF NOT EXISTS audience smallint NOT NULL DEFAULT 0;
