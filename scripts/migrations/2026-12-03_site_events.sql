-- The site's "What's happening" events (helpers/site_event_store.py, helpers/happening_box.py): scores, polls and
-- predictions, countdowns, elections, alerts, news and major events, and what an admin adds to the calendar. (The
-- observances, equinoxes, moon phases and festivals are worked out by helpers/calendar_rules.py and are not stored.)
-- An event is a row of ONE of two kinds of time: a civil date (a holiday is the same day in every time zone:
-- starts_date / ends_date) or an instant (a kick-off, an equinox: starts_utc / ends_utc). Times are bigint: every older
-- *_utc column is a 32-bit count of seconds that runs out in 2038 and an eclipse table goes past it.
-- Alerts, news and elections are link-out only (a link on https is required by the table) and an alert always expires.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-03_site_events.sql
CREATE TABLE IF NOT EXISTS public.site_events (
    id SERIAL PRIMARY KEY,
    kind character varying(12) NOT NULL,
    title character varying(200) NOT NULL,
    title_lang character varying(12) DEFAULT '' NOT NULL,
    details character varying(600) DEFAULT '' NOT NULL,
    link_url character varying(300) DEFAULT '' NOT NULL,
    date_kind character varying(8) DEFAULT 'instant' NOT NULL,
    starts_date date,
    ends_date date,
    starts_utc bigint,
    ends_utc bigint,
    expires_utc bigint,
    show_from_utc bigint,
    status character varying(10) DEFAULT 'scheduled' NOT NULL,
    status_changed_utc bigint DEFAULT 0 NOT NULL,
    state text DEFAULT '{}' NOT NULL,
    priority smallint DEFAULT 0 NOT NULL,
    featured boolean DEFAULT false NOT NULL,
    tentative boolean DEFAULT false NOT NULL,
    source character varying(24) DEFAULT 'admin' NOT NULL,
    source_key character varying(120) NOT NULL,
    last_seen_utc bigint DEFAULT 0 NOT NULL,
    fetched_utc bigint DEFAULT 0 NOT NULL,
    locked_utc bigint DEFAULT 0 NOT NULL,
    poll_post_id integer,
    country character(2),
    subdivision character varying(8),
    word_severity smallint DEFAULT 0 NOT NULL,
    word_filter_version character varying(12),
    is_sensitive boolean DEFAULT false NOT NULL,
    hidden_utc bigint DEFAULT 0 NOT NULL,
    hidden_by_id integer,
    created_by_id integer,
    updated_by_id integer,
    created_utc bigint DEFAULT 0 NOT NULL,
    updated_utc bigint DEFAULT 0 NOT NULL,
    CONSTRAINT site_events_source_key UNIQUE (source, source_key),
    CONSTRAINT site_events_kind CHECK (kind IN ('score', 'poll', 'prediction', 'countdown', 'election', 'alert', 'news', 'event', 'holiday', 'observance', 'astronomy')),
    CONSTRAINT site_events_status CHECK (status IN ('scheduled', 'live', 'final', 'postponed', 'cancelled', 'ended')),
    CONSTRAINT site_events_date_kind CHECK ((date_kind = 'civil' AND starts_date IS NOT NULL AND starts_utc IS NULL) OR (date_kind = 'instant' AND starts_utc IS NOT NULL AND starts_date IS NULL)),
    CONSTRAINT site_events_order CHECK ((ends_utc IS NULL OR ends_utc >= starts_utc) AND (ends_date IS NULL OR ends_date >= starts_date)),
    CONSTRAINT site_events_link CHECK (link_url = '' OR link_url LIKE 'https://%'),
    CONSTRAINT site_events_link_out CHECK (kind NOT IN ('alert', 'news', 'election') OR link_url LIKE 'https://%'),
    CONSTRAINT site_events_alert_expires CHECK (kind <> 'alert' OR expires_utc IS NOT NULL),
    CONSTRAINT site_events_country CHECK (country IS NULL OR length(country) = 2)
);

CREATE INDEX IF NOT EXISTS site_events_status_idx ON public.site_events (status, starts_utc);
CREATE INDEX IF NOT EXISTS site_events_date_idx ON public.site_events (starts_date);
CREATE INDEX IF NOT EXISTS site_events_kind_idx ON public.site_events (kind);

-- who changed what, for the admin page: manual scores and election entries are the site's own speech
CREATE TABLE IF NOT EXISTS public.site_event_log (
    id SERIAL PRIMARY KEY,
    event_id integer NOT NULL REFERENCES public.site_events(id) ON DELETE CASCADE,
    admin_id integer,
    action character varying(24) NOT NULL,
    detail character varying(400) DEFAULT '' NOT NULL,
    created_utc bigint DEFAULT 0 NOT NULL
);

CREATE INDEX IF NOT EXISTS site_event_log_event_idx ON public.site_event_log (event_id, id);
