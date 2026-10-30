-- The word filter reads every script (helpers/wordfilter.py), and its entries say which language they belong to:
-- NULL for none in particular (the English starter list, an admin's own words), else a code from
-- helpers/languages.LANGUAGE_NAMES. It never changes how a word is matched. The built-in words for other languages
-- (helpers/wordfilter_intl.json, from LDNOOBW) are added to a list that already has entries from /admin/word_filter
-- or `PYTHONPATH=. python scripts/import_intl_word_list.py --once`. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-07_word_filter_language.sql
ALTER TABLE word_filter_entries ADD COLUMN IF NOT EXISTS language character varying(16) DEFAULT NULL;
