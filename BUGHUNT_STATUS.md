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
  **before** starting the new code), `2026-11-23_owed_never_below_zero.sql`, `2026-11-24_paypal_reversals.sql`, `2026-11-25_deleted_accounts_app_grants.sql`.

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

| L5 | Pictures in a private guild follow the post page's own rule (`circles.PRIVATE_GUILD`), and the CDN forgets them when the guild turns private or a post there is unpinned |
| Round 2 | From the reviews (commits `77bcbf21c..`): account deletion ends app grants (migration `2026-11-25_deleted_accounts_app_grants.sql`); admins act only on accounts below their level, and the siege checks the account it installs; API follow / join need the `update` scope; Spaces: someone removed or blocked stays out, and only a call has a stage, roster and co-hosts; shared drafts keep no banned link and a suspended account publishes nothing; encrypted guild channel removals never redact state; unblocking puts a 1:1 chat back; public channels keep only signed messages and emoji reactions; a call heartbeat without Redis isn't a 500; a group invitation can't be accepted across a block with a member; a Highlight's older stories can be viewed and reported, and a closed profile's stories (and pictures) aren't served; collab drafts and proposals take their ids with RETURNING; admin email lookup with `_`, badge grant redirects; OAuth codes expire after 10 minutes, the redirect is encoded, the localhost check compares the host, and odd input / non-ASCII OIDC credentials are refusals, not 500s. After a review of round 2: an unblock never joins a request's recipient and survives an unreachable chat server; `/admin/user_ipban` shares the fixed ipnuke body; admins read a call's chat |
| Round 3 | From three hunts (guilds; posts and feeds; accounts): the wiki editor never starts from removed or filtered text, and a removed revision's reason leaves History; a removed member of a private guild no longer finds its posts (14 contributor subqueries ignored `is_active`); the exile check never reveals who wrote an anonymous item and counts forwarders; the v2 contributor DELETE works; private guilds never leave a curation (publishing, forking, its feed); reports and bookmarks read posts as the member; renewals, renames and tips respect what an account owes and never say who blocked whom; poll votes respect blocks; two-factor off by recovery code ends other sessions; a rejected own-account automation no longer blocks the member's scripts; guild privacy clears cached lists; re-invited guildmasters keep seniority order; email links re-check the address; bot feed adds are rate limited; odd input (guild names ending in a newline, long descriptions, huge ids and pages) is no longer a 500 |
| More | A new comment with a banned link is 400/403, not 401; banners, thumbnails and admin image tools use the site's bucket, never `i.ruqqus.com` (the bucket has no default now); links need a host (`example.org` without https:// works); sign-up's odd fields; the 503 page's Rocket Loader; old X / Instagram templates loaded their scripts; `recomputes.py` steps roll back; invisible characters in code are escapes |

Also removed (dead code): `docs/`, four unused assets, five unused scripts, eight unused templates, the old data export, commented-out feed routes, `python-daemon`, duplicate imports. Guards added: no raw invisible characters in app code, no redirect straight to a request value, no `i.ruqqus.com` address, no CDN script without a pinned version.

## Still open (start here)

1. **L10**: logged-out pages send `Cache-Control: public`; five tests in `tests/test_wrappers.py` pin it, so ask the
   owner before changing them.
2. A non-ASCII form key is a 500 (`hmac.compare_digest`); `tests/test_votes.py::test_post_non_ascii_formkey_is_500`
   pins it (owner's OK needed).
3. M9: the query retry wrapper in `__main__.py` silently drops pending changes; left on purpose (no failing flow found).
4. PayPal payload shapes follow PayPal's published v2 schema and have **not** been checked against real payloads.
5. The Terms page still names ruqqus.com (the owner's text).
6. Not done, on the owner's choice: the reorganisation into packages (it would conflict with parallel work).
7. A call's chat is readable only by the people in the call, the ones running it and admins; once a call has ended nobody else reads it (even when recorded). The owner decides whether a recorded call's chat stays readable.
8. OAuth codes that are never swapped stay in `client_auths` (refused after ten minutes; nothing sweeps them).
9. Someone else's private curation answers 403 where a missing one answers 404, so slugs can be probed; `tests/test_curation_algorithm_assets.py` pins both 403s (owner's OK needed to change them).
10. A vote refused across a block says "users who are blocking you"; `tests/test_votes.py` and `tests/test_votes_copies_route.py` pin that text (owner's OK needed; tips and polls now give one neutral message).
11. `GET /post_type` and `GET /feed_filter` change the session's feed filters without a form key, like `/regional` and `/language`: another site could change a visitor's filters (looks like a design choice).
