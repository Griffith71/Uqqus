-- Taxonomy clean-up (the owner, 2026-10-30 and 2026-10-31): broader, less repetitive subcategories in Politics, Culture, Sports, Science,
-- Memes, Technology and News, and a name of its own for every subcategory. Fifteen rows are renamed in place (their ids, and the guilds
-- filed under them, stay) and sixteen are merged into a row that already exists and then removed: no new row, no new id, so the sequence
-- is untouched.
--
--   Culture     125 LGBTQ                         -> Modern Culture
--   Politics    49 Left                           -> Political Ideas & Debate  (50 Right, 51 Authoritarian, 52 Libertarian, 176 Centrist merge in)
--               174 Government & Policy           <- 171 US Politics, 173 Elections
--               172 World Politics                -> International Affairs
--               108 Identity Politics             -> Social Issues  (it stops being the "unlisted" bucket: the code no longer special-cases it)
--   Sports      67 Combat                         -> Combat Sports  (194 Boxing & MMA merges in)
--               91 Team                           -> Other Sports   (90 Individual, 93 Partner merge in)
--   Science     98 Hard Sciences                  -> General Science (100 Natural Sciences merges in), 99 Soft Sciences -> Social Sciences
--   Memes       41 Casual                         -> General Memes   (42 Dank, 165 Interesting merge in)
--   Technology  97 Engineering                    <- 96 Mechanics,   75 Help -> Tech Support
--   News        45 Local, 46 North America        -> the guilds filed under News alone (cat_id 11, subcat_id 0): it is the kind of news that
--                                                    matters, and where it is about is the region filter's job
--   One name each   four subcategories were all called "News", two "Film & TV" (a name shows without its category in a notice, in a
--               search and in a topic): 23 -> Entertainment News, 55 -> Political News, 60 -> Science News, 74 -> Tech News,
--               8 Film & TV (Arts) merges into 24 Film & TV (Entertainment); 28 Gaming news -> Gaming News, to match.
--
-- A merge moves what holds the retired id before the row goes: boards.subcat_id, curations.category_filter (a comma-separated list, kept
-- sorted and without repeats) and the "categories" of a member's saved default filters (users.feed_defaults, JSON). A NEGATIVE target is a
-- category: -11 is "the guilds filed under category 11 alone", so a guild on it gets subcat_id 0 and cat_id 11, and a filter that named it
-- names -11 (see helpers/category_filter.py). A row is deleted only once no board holds it. A session's `catids` cookie cannot be migrated
-- here, but the app reads a retired id as the one it went into (topics.RETIRED_SUBCATS), and a saved Advanced filter's topic names
-- (`in:#left`) are read through topics.RETIRED the same way.
-- Workers still running the old code can make or move a guild onto a retired id between this file and the restart: that row then survives
-- (the delete skips a row a board holds). Restart the app, then run this file once more by hand (`psql -1 -v ON_ERROR_STOP=1 -f`: one transaction, so a guard that fires changes nothing; it is safe to run twice).
--   PYTHONPATH=. python scripts/migrate.py --apply
-- Fresh databases get the final rows from seed-db.sql. Safe to run twice. The app reads the categories once when it starts (and so do its
-- background jobs), so restart all of it afterwards.

CREATE TEMP TABLE taxonomy_cleanup_rename (id integer, cat_id integer, old_name character varying(64), new_name character varying(64));
INSERT INTO taxonomy_cleanup_rename VALUES
    (125, 3, 'LGBTQ', 'Modern Culture'),
    (49, 12, 'Left', 'Political Ideas & Debate'),
    (172, 12, 'World Politics', 'International Affairs'),
    (108, 12, 'Identity Politics', 'Social Issues'),
    (67, 14, 'Combat', 'Combat Sports'),
    (91, 14, 'Team', 'Other Sports'),
    (98, 13, 'Hard Sciences', 'General Science'),
    (99, 13, 'Soft Sciences', 'Social Sciences'),
    (41, 10, 'Casual', 'General Memes'),
    (75, 15, 'Help', 'Tech Support'),
    (23, 5, 'News', 'Entertainment News'),
    (55, 12, 'News', 'Political News'),
    (60, 13, 'News', 'Science News'),
    (74, 15, 'News', 'Tech News'),
    (28, 6, 'Gaming news', 'Gaming News');

CREATE TEMP TABLE taxonomy_cleanup_merge (old_id integer, cat_id integer, old_name character varying(64), new_id integer);
INSERT INTO taxonomy_cleanup_merge VALUES
    (50, 12, 'Right', 49),
    (51, 12, 'Authoritarian', 49),
    (52, 12, 'Libertarian', 49),
    (176, 12, 'Centrist', 49),
    (171, 12, 'US Politics', 174),
    (173, 12, 'Elections', 174),
    (194, 14, 'Boxing & MMA', 67),
    (90, 14, 'Individual', 91),
    (93, 14, 'Partner', 91),
    (100, 13, 'Natural Sciences', 98),
    (42, 10, 'Dank', 41),
    (165, 10, 'Interesting', 41),
    (96, 15, 'Mechanics', 97),
    (8, 1, 'Film & TV', 24),
    (45, 11, 'Local', -11),
    (46, 11, 'North America', -11);

-- Guard: a row that has one of these ids must be the one we mean (its old or its new name, in the right category), and every row a merge
-- moves guilds into must exist (once the renames are done, below).
DO $$
DECLARE
    clash text;
BEGIN
    SELECT string_agg(format('subcategory %s is %L in category %s, expected %L or %L in category %s',
                             s.id, s.name, s.cat_id, r.old_name, r.new_name, r.cat_id), '; ' ORDER BY s.id) INTO clash
    FROM subcategories s JOIN taxonomy_cleanup_rename r ON r.id = s.id
    WHERE s.name NOT IN (r.old_name, r.new_name) OR s.cat_id IS DISTINCT FROM r.cat_id;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'taxonomy cleanup: %', clash;
    END IF;

    SELECT string_agg(format('subcategory %s is %L in category %s, expected %L in category %s',
                             s.id, s.name, s.cat_id, m.old_name, m.cat_id), '; ' ORDER BY s.id) INTO clash
    FROM subcategories s JOIN taxonomy_cleanup_merge m ON m.old_id = s.id
    WHERE s.name IS DISTINCT FROM m.old_name OR s.cat_id IS DISTINCT FROM m.cat_id;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'taxonomy cleanup: %', clash;
    END IF;
END $$;

UPDATE subcategories s SET name = r.new_name
FROM taxonomy_cleanup_rename r
WHERE s.id = r.id AND s.cat_id = r.cat_id AND s.name = r.old_name;

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(m.new_id::text, ', ') INTO missing
    FROM (SELECT DISTINCT new_id FROM taxonomy_cleanup_merge) m
    WHERE (m.new_id > 0 AND NOT EXISTS (SELECT 1 FROM subcategories s WHERE s.id = m.new_id))
       OR (m.new_id < 0 AND NOT EXISTS (SELECT 1 FROM categories c WHERE c.id = -m.new_id));
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'taxonomy cleanup: no subcategory (or, for a negative id, category) % to merge into', missing;
    END IF;
END $$;

-- 1. the guilds
UPDATE boards b SET subcat_id = GREATEST(m.new_id, 0), cat_id = CASE WHEN m.new_id < 0 THEN -m.new_id ELSE NULL END
FROM taxonomy_cleanup_merge m
WHERE b.subcat_id = m.old_id;

-- 2. the curations' category filters: each token is an integer (a subcategory, 0 for Uncategorized, -N for a whole category); a retired id
--    becomes the row it merged into, and the list stays sorted without repeats
UPDATE curations c
SET category_filter = array_to_string(ARRAY(
        SELECT DISTINCT COALESCE(m.new_id, t.id) AS v
        FROM (SELECT trim(tok)::integer AS id FROM unnest(string_to_array(c.category_filter, ',')) AS tok
              WHERE trim(tok) ~ '^-?[0-9]{1,9}$') t
        LEFT JOIN taxonomy_cleanup_merge m ON m.old_id = t.id
        ORDER BY v), ',')
WHERE c.category_filter IS NOT NULL AND c.category_filter <> ''
  AND EXISTS (SELECT 1 FROM unnest(string_to_array(c.category_filter, ',')) AS tok
              JOIN taxonomy_cleanup_merge m ON trim(tok) ~ '^-?[0-9]{1,9}$' AND trim(tok)::integer = m.old_id);

-- 3. members' saved default filters: {"categories": [ints], ...} as text. A row that does not parse as JSON is left as it is (the app reads
--    it as "no default"); the categories keep their order and lose repeats.
DO $$
DECLARE
    u record;
    fixed jsonb;
BEGIN
    FOR u IN SELECT id, feed_defaults FROM users
             WHERE feed_defaults IS NOT NULL AND feed_defaults <> '' AND feed_defaults ~ '[0-9]'
               AND EXISTS (SELECT 1 FROM taxonomy_cleanup_merge m          -- only a default that holds a retired id as a number of its own
                           WHERE users.feed_defaults ~ ('(^|[^0-9-])' || m.old_id || '([^0-9]|$)'))
    LOOP
        BEGIN
            fixed := u.feed_defaults::jsonb;
            IF jsonb_typeof(fixed) = 'object' AND jsonb_typeof(fixed -> 'categories') = 'array'
               AND EXISTS (SELECT 1 FROM jsonb_array_elements(fixed -> 'categories') e
                           JOIN taxonomy_cleanup_merge m ON jsonb_typeof(e) = 'number' AND e::text = m.old_id::text) THEN
                fixed := jsonb_set(fixed, '{categories}', (
                    SELECT COALESCE(jsonb_agg(v ORDER BY first_at), '[]'::jsonb)
                    FROM (SELECT v, min(ord) AS first_at
                          FROM (SELECT COALESCE(to_jsonb(m.new_id), e.value) AS v, e.ordinality AS ord
                                FROM jsonb_array_elements(fixed -> 'categories') WITH ORDINALITY e
                                LEFT JOIN taxonomy_cleanup_merge m ON jsonb_typeof(e.value) = 'number' AND e.value::text = m.old_id::text) x
                          GROUP BY v) y));
                UPDATE users SET feed_defaults = fixed::text WHERE id = u.id AND feed_defaults = u.feed_defaults;       -- not over a default saved a moment ago
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
        END;
    END LOOP;
END $$;

-- 4. the retired rows, once nothing holds them
DELETE FROM subcategories s
USING taxonomy_cleanup_merge m
WHERE s.id = m.old_id AND s.cat_id = m.cat_id AND s.name = m.old_name
  AND NOT EXISTS (SELECT 1 FROM boards b WHERE b.subcat_id = s.id);

DROP TABLE taxonomy_cleanup_merge;
DROP TABLE taxonomy_cleanup_rename;
