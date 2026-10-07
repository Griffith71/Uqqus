-- Four languages nobody speaks in daily life any more were taken off the language list
-- (helpers/languages.py REMOVED_LANGUAGES): Ancient Greek (grc), Ancient Hebrew (hbo),
-- Latin (la) and Volapuk (vo). Data only, no schema change.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-08_languages.sql
-- Safe to run twice.

-- posts tagged with one of them become untagged, as very short posts are
UPDATE submissions SET language_code = NULL WHERE language_code IN ('grc', 'hbo', 'la', 'vo');

-- curations that filtered by one of them forget it (a list of codes separated by commas)
UPDATE curations
SET language_filter = COALESCE((
    SELECT string_agg(code, ',' ORDER BY ord)
    FROM unnest(string_to_array(language_filter, ',')) WITH ORDINALITY AS t(code, ord)
    WHERE code <> '' AND code NOT IN ('grc', 'hbo', 'la', 'vo')
), '')
WHERE language_filter ~ '(^|,)(grc|hbo|la|vo)(,|$)';
