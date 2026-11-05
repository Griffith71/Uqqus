-- Private posting (helpers/private_posting.py; the owner, 2026-11-05): when on, everything the account posts goes only to its Friends
-- circle (mutual followers) and its custom circles hold only mutual followers. On by default for a locked account (age_lock set), which
-- may turn it off itself (a parent's consent for a sub-account, Stage 2). Not the same as users.is_private ("Private mode"), which hides
-- the whole profile. Safe to run twice.
--   PYTHONPATH=. python scripts/migrate.py --apply
ALTER TABLE users ADD COLUMN IF NOT EXISTS private_posting boolean DEFAULT false NOT NULL;
