-- Regions: Mali joins Guinea Coast; Sahel / Erythrean Horn / Equatorial Nile reshuffle.
-- There is no migration framework: run this once against an existing database
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-06_regions_reshuffle.sql
-- Fresh databases get the same rows from seed-db.sql. Safe to run twice.
-- Country membership itself lives in ruqqus/helpers/regions.py (COUNTRY_TO_REGION).

-- Votes and proposals for the two regions whose country sets changed no longer
-- mean anything, so clear them before resetting the names.
DELETE FROM region_name_votes WHERE region_id IN (SELECT id FROM regions WHERE code IN ('sahel', 'horn_of_africa'));
DELETE FROM region_name_proposals WHERE region_id IN (SELECT id FROM regions WHERE code IN ('sahel', 'horn_of_africa'));

UPDATE regions SET default_name = 'The Sahel', current_name = 'The Sahel' WHERE code = 'sahel';
UPDATE regions SET default_name = 'Erythrean Horn', current_name = 'Erythrean Horn' WHERE code = 'horn_of_africa';

INSERT INTO regions (code, default_name, current_name, color)
VALUES ('equatorial_nile', 'Equatorial Nile', 'Equatorial Nile', '#6a3d9a')
ON CONFLICT (code) DO NOTHING;

-- Re-key past logins of the countries that moved.
UPDATE login_events SET region_code = CASE upper(cf_country)
    WHEN 'ML' THEN 'west_africa'
    WHEN 'EH' THEN 'sahel'
    WHEN 'SD' THEN 'sahel'
    WHEN 'TD' THEN 'sahel'
    WHEN 'KE' THEN 'equatorial_nile'
    WHEN 'UG' THEN 'equatorial_nile'
    WHEN 'SS' THEN 'equatorial_nile'
END
WHERE upper(cf_country) IN ('ML', 'EH', 'SD', 'TD', 'KE', 'UG', 'SS');

-- Move users whose displayed region came from one of those countries: use the
-- region of their most recent login.
UPDATE users u SET display_region = latest.region_code
FROM (
    SELECT DISTINCT ON (user_id) user_id, region_code, cf_country
    FROM login_events
    WHERE region_code IS NOT NULL
    ORDER BY user_id, created_utc DESC
) latest
WHERE latest.user_id = u.id
  AND upper(latest.cf_country) IN ('ML', 'EH', 'SD', 'TD', 'KE', 'UG', 'SS')
  AND u.display_region IS DISTINCT FROM latest.region_code
  AND u.display_region IN ('sahel', 'horn_of_africa', 'north_africa', 'west_africa');

SELECT setval('regions_id_seq', (SELECT max(id) FROM regions));
