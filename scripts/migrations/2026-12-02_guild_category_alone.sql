-- A guild may choose its broad category alone: boards.cat_id is set only for such a guild, which then has subcat_id = 0;
-- every other guild keeps it NULL and its category comes from its subcategory. No foreign key, like subcat_id.
-- Also takes out the 27 "General <Category>" subcategories (ids 306-332) of an earlier design, on the databases that
-- have them: a guild filed under one moves to its category and the rows go. A row is one of them only by its id
-- (305 + the category's id) and its name together, so a database that never had them is left as it is. Nothing in a
-- curation or a saved default is rewritten: an id from 306 to 332 left in one matches nothing and is dropped when read.
--   PYTHONPATH=. python scripts/migrate.py --apply
-- Fresh databases get the same from schema.sql and seed-db.sql. Safe to run twice. The app reads the categories once
-- when it starts (and so do its background jobs), so restart all of it afterwards.

ALTER TABLE boards ADD COLUMN IF NOT EXISTS cat_id integer;
CREATE INDEX IF NOT EXISTS boards_cat_idx ON boards (cat_id);

UPDATE boards SET subcat_id = 0, cat_id = s.cat_id
FROM subcategories s JOIN categories c ON c.id = s.cat_id
WHERE boards.subcat_id = s.id AND s.id = 305 + s.cat_id AND s.name = 'General ' || c.name;

DELETE FROM subcategories s USING categories c
WHERE c.id = s.cat_id AND s.id = 305 + s.cat_id AND s.name = 'General ' || c.name
  AND NOT EXISTS (SELECT 1 FROM boards b WHERE b.subcat_id = s.id);

SELECT setval('subcategories_id_seq', (SELECT max(id) FROM subcategories));
