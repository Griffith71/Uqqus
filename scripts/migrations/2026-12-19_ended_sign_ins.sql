-- Sign-ins that ended stay ended (helpers/sign_in_ends.py, chat plan phase 2 step 1). A row is a SHA-256 of a cookie's random
-- `session_id`, never the id itself: Log out, a sign-in over an older one in the same browser, the QR sign-in's password and closing
-- your own account write one, and a cookie whose sign-in is here is refused (wrappers.get_logged_in_user) and never written back
-- (__main__.after_request). Kept 400 days (helpers/retention.py). No foreign key to users (the project's rule). Safe to run twice.
--   PYTHONPATH=. python scripts/migrate.py --apply
CREATE TABLE IF NOT EXISTS ended_sign_ins (
    id character(64) PRIMARY KEY,
    user_id integer NOT NULL,
    ended_utc bigint NOT NULL,
    reason character varying(16) NOT NULL
);
CREATE INDEX IF NOT EXISTS ended_sign_ins_ended ON ended_sign_ins (ended_utc);
