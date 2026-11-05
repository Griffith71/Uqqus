-- Parent and child accounts (helpers/child_accounts.py, helpers/child_store.py; the owner, 2026-11-05). A parent (an age-confirmed adult) makes a
-- child sub-account from their own Settings (users.age_lock = 2). `account_links` says whose it is (one parent a child, up to 5 a parent);
-- `child_controls` holds what the parent has switched on or off for that child: chat, Spaces, money and "anonymous posting and guilds" start OFF,
-- posting and commenting start ON. `ask_private_utc` and `ask_release_utc` are the child's open requests to the parent (to turn Private posting
-- off, and to be released as an adult after saying 18 or over where a tick is accepted); 0 means none; `ask_release_country` is where the child asked from
-- (whether a tick was accepted is the child's country's rule, not the parent's). The suspension the site gives a child because its parent was suspended is
-- remembered on `child_family` (below), which outlives the link. Neither table has a foreign key to users
-- (the project's rule), so closing an account deletes its rows (helpers/account_cleanup.py). Safe to run twice.
--   PYTHONPATH=. python scripts/migrate.py --apply
CREATE TABLE IF NOT EXISTS account_links (
    child_id integer PRIMARY KEY,
    parent_id integer NOT NULL,
    created_utc integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS account_links_parent_idx ON account_links (parent_id);

CREATE TABLE IF NOT EXISTS child_controls (
    child_id integer PRIMARY KEY,
    chat boolean NOT NULL DEFAULT false,
    spaces boolean NOT NULL DEFAULT false,
    money boolean NOT NULL DEFAULT false,
    anonymous_guilds boolean NOT NULL DEFAULT false,
    posting boolean NOT NULL DEFAULT true,
    commenting boolean NOT NULL DEFAULT true,
    ask_private_utc integer NOT NULL DEFAULT 0,
    ask_release_utc integer NOT NULL DEFAULT 0,
    ask_release_country character varying(2),
    updated_utc integer NOT NULL DEFAULT 0
);
-- a database that ran this file before it was finished
ALTER TABLE child_controls ADD COLUMN IF NOT EXISTS ask_release_country character varying(2);
ALTER TABLE child_controls DROP COLUMN IF EXISTS suspended_until_utc;

-- Every child account a parent has made, kept when a link ends (a release, stopping, a deleted child): the limits count what was made, not what is still
-- linked (or stopping and deleting would make the limit free), and a parent, its children and their siblings are never alts of each other. `child_id` is
-- emptied when that child's account closes: the row then only counts. A parent's closing deletes its rows. No foreign key to users.
-- `suspended_until_utc` is the end of the suspension the site gave the child because its parent was suspended (0: none) and `prior_until_utc` the end of a
-- shorter ban the child already had for its own conduct, which comes back when the parent's is lifted: here, not on the controls, because a link can end while
-- the parent is still suspended and the parent's unban must still find the child.
CREATE TABLE IF NOT EXISTS child_family (
    id serial PRIMARY KEY,
    parent_id integer NOT NULL,
    child_id integer,
    made_utc integer NOT NULL DEFAULT 0,
    suspended_until_utc integer NOT NULL DEFAULT 0,
    prior_until_utc integer NOT NULL DEFAULT 0
);
ALTER TABLE child_family ADD COLUMN IF NOT EXISTS suspended_until_utc integer NOT NULL DEFAULT 0;
ALTER TABLE child_family ADD COLUMN IF NOT EXISTS prior_until_utc integer NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS child_family_child_key ON child_family (child_id) WHERE child_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS child_family_parent_idx ON child_family (parent_id, made_utc);

-- links made before the ledger existed count in it too (a no-op on a database that never had one)
INSERT INTO child_family (parent_id, child_id, made_utc)
SELECT l.parent_id, l.child_id, l.created_utc FROM account_links l
WHERE NOT EXISTS (SELECT 1 FROM child_family f WHERE f.child_id = l.child_id);
