-- Private posting is gone (the owner, 2026-11-05, decision 14 of the Log plan; the Log's slice D5). A child account is read-only now: it can't post or
-- comment at all (helpers/age_declaration.py `content_refusal`), so there is nothing for a "Friends circle only" setting to govern. The column this file
-- first made, `users.private_posting`, is dropped here (the site has not launched, so this file was amended rather than followed by another that undoes
-- it: a database made from schema.sql never had the column, and this must change nothing there). Safe to run twice.
--   PYTHONPATH=. python scripts/migrate.py --apply
ALTER TABLE users DROP COLUMN IF EXISTS private_posting;
