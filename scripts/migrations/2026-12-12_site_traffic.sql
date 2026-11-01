-- The site's own count of requests for its pages (helpers/traffic.py, helpers/traffic_store.py): one number per UTC day for
-- each kind of page, each referring host and each country, counted apart, never crossed. Requests, not people: no id, name,
-- address, user agent or path, and no foreign keys. Pruned after 400 days (helpers/retention.py). `site_traffic_flushes`
-- names the batches already written, so a flush that failed after its commit is not counted twice. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-12_site_traffic.sql
CREATE TABLE IF NOT EXISTS public.site_traffic (
    day integer NOT NULL,
    dimension character varying(10) NOT NULL CHECK (dimension IN ('kind', 'referrer', 'country')),
    value character varying(100) NOT NULL,
    requests bigint DEFAULT 0 NOT NULL,
    PRIMARY KEY (day, dimension, value)
);

CREATE TABLE IF NOT EXISTS public.site_traffic_flushes (
    batch character varying(32) PRIMARY KEY,
    flushed_utc bigint NOT NULL
);
