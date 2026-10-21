-- Three small additions (the owner, 2026-10-20). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-12_defaults_descriptions_bells.sql
--
-- users.feed_defaults: a member's saved feed filters (Regional, Language, Categorical, Post type, Advanced filter) as
--   JSON, loaded into a new session once (helpers/feed_filters.py read_defaults; routes/front.py).
-- circles.description: what the owner of a paid circle says subscribers get, shown on the subscribe sheet
--   (partials/circle_subscribe_modal.html). Plain text, rated by the word filter (description_severity).
-- bell_rings: which (actor, kind, target) has already rung the bells, so turning a herald or a pin off and on, or
--   forwarding the same post again, never rings twice (helpers/bell.py). Kept 90 days (helpers/retention.py).
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS feed_defaults text DEFAULT '' NOT NULL;

ALTER TABLE public.circles ADD COLUMN IF NOT EXISTS description character varying(300) DEFAULT '' NOT NULL;
ALTER TABLE public.circles ADD COLUMN IF NOT EXISTS description_severity smallint DEFAULT 0 NOT NULL;

CREATE TABLE IF NOT EXISTS public.bell_rings (
    id SERIAL PRIMARY KEY,
    actor_id integer NOT NULL,
    kind character varying(16) NOT NULL,
    target_id integer NOT NULL,
    created_utc integer NOT NULL,
    CONSTRAINT bell_rings_once UNIQUE (actor_id, kind, target_id)
);

CREATE INDEX IF NOT EXISTS bell_rings_created_idx ON public.bell_rings (created_utc);
