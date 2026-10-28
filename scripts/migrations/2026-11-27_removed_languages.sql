-- Esperanto and the pidgins and creoles were taken off the language list (the owner, 2026-10-24;
-- helpers/languages.py REMOVED_LANGUAGES): Esperanto (eo), Haitian Creole (ht), Nigerian Pidgin (pcm),
-- Guadeloupean Creole French (gcf) and Guianese Creole French (gcr). Data only, no schema change.
--   PYTHONPATH=. python scripts/migrate.py --apply
-- Safe to run twice. Afterwards scripts/redetect_languages.py --once may name some of these posts again
-- (as the nearest listed language, when the detector is sure of it).

-- posts and comments tagged with one of them become untagged, as very short ones are
UPDATE submissions SET language_code = NULL WHERE language_code IN ('eo', 'ht', 'pcm', 'gcf', 'gcr');
UPDATE comments SET language_code = NULL WHERE language_code IN ('eo', 'ht', 'pcm', 'gcf', 'gcr');

-- curations that filtered by one of them, and members who read one, forget it (lists of codes separated by commas)
UPDATE curations
SET language_filter = COALESCE((
    SELECT string_agg(code, ',' ORDER BY ord)
    FROM unnest(string_to_array(language_filter, ',')) WITH ORDINALITY AS t(code, ord)
    WHERE code <> '' AND code NOT IN ('eo', 'ht', 'pcm', 'gcf', 'gcr')
), '')
WHERE language_filter ~ '(^|,)(eo|ht|pcm|gcf|gcr)(,|$)';

UPDATE users
SET languages = COALESCE((
    SELECT string_agg(code, ',' ORDER BY ord)
    FROM unnest(string_to_array(languages, ',')) WITH ORDINALITY AS t(code, ord)
    WHERE code <> '' AND code NOT IN ('eo', 'ht', 'pcm', 'gcf', 'gcr')
), '')
WHERE languages ~ '(^|,)(eo|ht|pcm|gcf|gcr)(,|$)';

-- a saved default filter (users.feed_defaults, JSON) is checked again whenever it is read (known_codes), so a removed
-- code in one is dropped there without changing it here
