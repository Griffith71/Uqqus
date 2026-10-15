-- Many circles (ruqqus/helpers/circles.py, helpers/circle_store.py). Until now an account had one Circle, with Close
-- Friends (added free) and Subscribers (paid) in it, and a post was for Subscribers (audience 1) or Close Friends (2).
-- Now an account has:
--   * Friends ('friends'): everyone it follows who follows it back, worked out when a post is read, so no members are
--     stored; the row exists only so a post can name it, and is made the first time it is chosen;
--   * custom circles it names and fills itself ('custom'; nobody is told they are in one);
--   * paid circles subscribers pay to be in ('paid'; making a new one needs Premium).
-- A post or a story is for everyone (audience 0) or for the circles listed for it (audience 4: post_circles,
-- story_circles). A Circle guild (audience 3) and its circle ('guild') are unchanged.
--
-- Existing data is converted so that nobody gains or loses access: each owner's Close Friends become a custom circle
-- "Close Friends"; their Circle becomes the paid circle "Subscribers", with the same price, subscribers and renewal
-- dates (dropped when it never asked a price and nobody ever paid); posts and stories for Subscribers go to both,
-- those for Close Friends to Close Friends. An old circle is one with no name yet, so running this again changes
-- nothing.

BEGIN;

ALTER TABLE circles ADD COLUMN IF NOT EXISTS name character varying(40) DEFAULT '' NOT NULL;
ALTER TABLE circles ADD COLUMN IF NOT EXISTS kind character varying(12) DEFAULT 'paid' NOT NULL;
UPDATE circles SET kind = 'guild' WHERE board_id IS NOT NULL AND kind <> 'guild';

DROP INDEX IF EXISTS circles_user_key;
ALTER TABLE circle_members DROP CONSTRAINT IF EXISTS circle_members_tier;
ALTER TABLE stories DROP CONSTRAINT IF EXISTS stories_audience;

CREATE TABLE IF NOT EXISTS post_circles (
    post_id integer NOT NULL,
    circle_id integer NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, circle_id)
);
CREATE INDEX IF NOT EXISTS post_circles_circle_idx ON post_circles USING btree (circle_id);

CREATE TABLE IF NOT EXISTS story_circles (
    story_id integer NOT NULL,
    circle_id integer NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    PRIMARY KEY (story_id, circle_id)
);
CREATE INDEX IF NOT EXISTS story_circles_circle_idx ON story_circles USING btree (circle_id);

-- data: begin (plain SQL: tests/test_many_circles_migration.py runs these statements on SQLite)
INSERT INTO circles (user_id, price_coins, created_utc, name, kind)
SELECT c.user_id, 0, c.created_utc, 'Close Friends', 'custom' FROM circles c
WHERE c.user_id IS NOT NULL AND c.kind = 'paid' AND c.name = ''
  AND EXISTS (SELECT 1 FROM circle_members m WHERE m.circle_id = c.id AND m.tier = 'friend');

UPDATE circle_members SET tier = 'member', circle_id = (
    SELECT n.id FROM circles n JOIN circles o ON o.user_id = n.user_id
    WHERE o.id = circle_members.circle_id AND n.kind = 'custom' AND n.name = 'Close Friends')
WHERE tier = 'friend' AND circle_id IN (SELECT id FROM circles WHERE kind = 'paid' AND name = '');

DELETE FROM circles WHERE kind = 'paid' AND name = '' AND price_coins = 0
  AND NOT EXISTS (SELECT 1 FROM circle_members m WHERE m.circle_id = circles.id)
  AND NOT EXISTS (SELECT 1 FROM circle_payments p WHERE p.circle_id = circles.id);

UPDATE circles SET name = 'Subscribers' WHERE kind = 'paid' AND name = '';

INSERT INTO post_circles (post_id, circle_id)
SELECT s.id, c.id FROM submissions s JOIN circles c ON c.user_id = s.author_id
WHERE (s.audience = 1 AND c.kind IN ('paid', 'custom')) OR (s.audience = 2 AND c.kind = 'custom');
UPDATE submissions SET audience = 4 WHERE audience IN (1, 2);

INSERT INTO story_circles (story_id, circle_id)
SELECT s.id, c.id FROM stories s JOIN circles c ON c.user_id = s.user_id
WHERE (s.audience = 1 AND c.kind IN ('paid', 'custom')) OR (s.audience = 2 AND c.kind = 'custom');
UPDATE stories SET audience = 4 WHERE audience IN (1, 2);
-- data: end

-- drafts keep their fields as JSON text: a draft for Subscribers or Close Friends names the same circles
UPDATE post_drafts d SET options = ((d.options::jsonb - 'audience') || jsonb_build_object(
    'audience', 4,
    'circles', COALESCE((SELECT jsonb_agg(c.id::text ORDER BY c.id) FROM circles c WHERE c.user_id = d.user_id
        AND (((d.options::jsonb ->> 'audience') = '1' AND c.kind IN ('paid', 'custom'))
          OR ((d.options::jsonb ->> 'audience') = '2' AND c.kind = 'custom'))), '[]'::jsonb)))::text
WHERE (d.options::jsonb ->> 'audience') IN ('1', '2');

ALTER TABLE circles ALTER COLUMN kind DROP DEFAULT;
ALTER TABLE circles DROP CONSTRAINT IF EXISTS circles_kind;
ALTER TABLE circles ADD CONSTRAINT circles_kind CHECK (kind IN ('friends', 'custom', 'paid', 'guild'));
ALTER TABLE circles DROP CONSTRAINT IF EXISTS circles_kind_owner;
ALTER TABLE circles ADD CONSTRAINT circles_kind_owner CHECK ((kind = 'guild') = (board_id IS NOT NULL));
CREATE UNIQUE INDEX IF NOT EXISTS circles_friends_key ON circles USING btree (user_id) WHERE (kind = 'friends');
CREATE UNIQUE INDEX IF NOT EXISTS circles_name_key ON circles USING btree (user_id, lower((name)::text)) WHERE (kind IN ('custom', 'paid'));
CREATE INDEX IF NOT EXISTS circles_user_idx ON circles USING btree (user_id) WHERE (user_id IS NOT NULL);
ALTER TABLE circle_members ADD CONSTRAINT circle_members_tier CHECK (tier IN ('member', 'subscriber'));
ALTER TABLE stories ADD CONSTRAINT stories_audience CHECK (audience IN (0, 4));
-- a paid circle can be deleted once nobody has paid days left in it: the record of what came in stays (the owner's
-- earnings are counted by owner_id)
ALTER TABLE circle_payments ALTER COLUMN circle_id DROP NOT NULL;
ALTER TABLE circle_payments DROP CONSTRAINT IF EXISTS circle_payments_circle_id_fkey;
ALTER TABLE circle_payments ADD CONSTRAINT circle_payments_circle_id_fkey FOREIGN KEY (circle_id) REFERENCES circles(id) ON DELETE SET NULL;

COMMIT;
