-- Languages and translation (the owner, 2026-10-23): a comment's detected language (so the Translate button knows
-- when to show), a member's own languages (Settings > Content > My languages), the translations kept so a text is
-- translated once per language (helpers/translation_store.py), and what an outside translator has been sent each month
-- (a budget that fails closed, like turn_usage). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-26_languages_and_translation.sql

CREATE TABLE IF NOT EXISTS public.schema_migrations (
    name character varying(255) PRIMARY KEY,
    applied_utc integer DEFAULT 0 NOT NULL
);

ALTER TABLE public.comments ADD COLUMN IF NOT EXISTS language_code character varying(5) DEFAULT NULL;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS languages character varying(200) DEFAULT '' NOT NULL;

CREATE TABLE IF NOT EXISTS public.translations (
    id serial PRIMARY KEY,
    kind character varying(10) NOT NULL,
    item_id integer NOT NULL,
    source_hash character varying(64) NOT NULL,
    source_lang character varying(5),
    target character varying(5) NOT NULL,
    engine character varying(20) NOT NULL,
    title text DEFAULT '' NOT NULL,
    text_html text DEFAULT '' NOT NULL,
    created_utc integer NOT NULL,
    CONSTRAINT translations_kind CHECK (kind IN ('post', 'comment'))
);
CREATE UNIQUE INDEX IF NOT EXISTS translations_key ON public.translations (kind, item_id, target, engine, source_hash);
CREATE INDEX IF NOT EXISTS translations_created_idx ON public.translations (created_utc);

CREATE TABLE IF NOT EXISTS public.translation_usage (
    month character varying(7) NOT NULL,
    engine character varying(20) NOT NULL,
    chars bigint DEFAULT 0 NOT NULL,
    PRIMARY KEY (month, engine)
);

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-11-26_languages_and_translation.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
