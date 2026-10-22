# Bug hunt 2026-10: where it stands

Branch `bughunt/2026-10`, made from `Laba-v0.1`. Every fix is its own commit, with a reproducing test that failed
before it (`git log afc86ec2a..` shows them; each commit message says what was wrong and how it was checked).
Project rules are in `CLAUDE.md`; read its **Rules** section first.

## How to check your work

- `.venv/Scripts/ruff check .` and `.venv/Scripts/python -m pytest -q` (on Windows 5 tests fail for local reasons:
  path separators, no libsass; they pass in the container and CI).
- Real Postgres: `tests/pg` (see **Operations** in `CLAUDE.md`). Most of this hunt's tests are in
  `tests/pg/test_bughunt_fixes.py`, and the flows it pinned first are in `tests/pg/test_characterization.py`.
- New migrations (apply to a database that has the older ones): `2026-11-22_escape_mod_log_notes.sql` (run it
  **before** starting the new code), `2026-11-23_owed_never_below_zero.sql`, `2026-11-24_paypal_reversals.sql`.

## Fixed

| | What |
|---|---|
| C1 | Stored XSS through a guild's hide reason and mod-log notes (and an admin nuke's ban reason) |
| H1, H2 | SSRF: the title suggestion and link previews read any address a member typed (now `helpers/page_fetch.py`) |
| H3 | PayPal refunds and chargebacks never reached the account; now matched, counted once, all-or-nothing |
| H4 | A 0-coin purchase locked accounts; what an account owes can't go below 0 (with a CHECK) |
| H5 | Open redirects after sign-in |
| H6 | Password reset accepted any password; bad reset links were 500s |
| H7 | Nothing loads from the old ruqqus.com domain; analytics from your own Plausible (`PLAUSIBLE_SRC`, off by default) |
| M1 | Guild CSS check bypasses (nested rules, `@font-face`, `image-set`) |
| M2 | An IP ban ran on a GET link; now a POST with the form key |
| M3 | Two votes at once gave a 500 |
| M4 | A notification sent from a background job could be lost |
| M5 | Background jobs stayed broken after one database error |
| M6 | Admin money page counted write-offs and refunds wrong |
| M7 | Account deletion skipped real errors (and a follow-up so linked storage doesn't block it) |
| M8 | Login CSRF (another site could sign a visitor in) |
| L1-L4, L6, L7 | Dead decorator, comment edit answers, 500s on bad query values, form key edge case, feed dates, CDN libraries pinned with SRI |

Also: `docs/` (the old Ruqqus site) and four unused files were removed.

## Still open (start here)

1. **L5, reverted**: pictures in comments in a private guild are served publicly to anyone holding the link. The fix
   must use the post page's own rule (`Submission.in_private_guild_for`), not the Circle-guild one.
2. **L10**: logged-out pages send `Cache-Control: public`; five tests in `tests/test_wrappers.py` pin it, so ask the
   owner before changing them.
3. A non-ASCII form key is a 500 (`hmac.compare_digest`); `tests/test_votes.py::test_post_non_ascii_formkey_is_500`
   pins it (owner's OK needed).
4. Creating a comment answers a banned link with 401 instead of 400 (`api_comment`).
5. M9: the query retry wrapper in `__main__.py` silently drops pending changes; left on purpose (no failing flow found).
6. Small: `S3_BUCKET_NAME` defaults to `i.ruqqus.com`; a leftover Rocket Loader script on `errors/503.html`;
   `site_embeds/twitter.html` loads X's widget; `recomputes.py` steps that don't roll back on error.
7. PayPal payload shapes follow PayPal's published v2 schema and have **not** been checked against real payloads.
8. Planned, not started: the rest of the dead-code list, the reorganisation into packages (start with `tests/paths.py`),
   CI guards (semgrep rules, a coverage floor), and a final review of the whole branch.
