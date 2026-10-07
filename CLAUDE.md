# Uqqus

Two independent apps in one repo. Do not mix imports or config between them.

|            | Path                                   | Stack                                                                                                                                                                          |
| ---------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| New app    | `web/`                                 | Next.js 16 App Router, React 19, TypeScript (strict), Tailwind 4 + shadcn/ui, Prisma 6 + Postgres (Supabase), Auth.js v5, Zod, Vitest, Playwright. Details in `web/CLAUDE.md`. |
| Legacy app | `ruqqus/` (+ `scripts/`, `schema.sql`) | Flask 3, SQLAlchemy 2, Jinja templates, Postgres, Redis, Matrix/Synapse chat (`synapse/`). Python 3.11 in Docker.                                                              |

Tooling: Node 22 (`.nvmrc`), Python dev tools in `.venv` (`pip install -r requirements-dev.txt`).
Env vars: copy `.env.example` (legacy) and `web/.env.example` (web) to `.env`; never commit `.env`.

## Commands

| Task                                   | Command                                                                |
| -------------------------------------- | ---------------------------------------------------------------------- |
| Everything (run before declaring done) | `bash scripts/check.sh`                                                |
| web: all checks                        | `cd web && npm run check` (typecheck, lint, format:check, test, build) |
| web: typecheck / lint / test / build   | `npm run typecheck` / `lint` / `test` / `build` (in `web/`)            |
| web: e2e smoke                         | `cd web && npm run test:e2e`                                           |
| web: fix formatting                    | `cd web && npm run format`                                             |
| legacy: lint / test                    | `.venv/Scripts/ruff check .` / `.venv/Scripts/python -m pytest -q`     |

Ruff on the legacy app is errors-only (`pyproject.toml`); there is no Python type checker.
The pre-commit hook (Husky) runs only staged-file checks: gitleaks, ruff, eslint, prettier, typecheck. CI runs the full suite.

## Terminology (applies to both apps, UI text and code names)

| Say                                                                                                                                 | Never say                                  |
| ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| **post**: always created first on the author's own profile. You cannot post to a guild.                                             | "post to/in a guild", "submit to a guild"  |
| **comment**: anything that is a reply, whether on a post or on another comment.                                                     | reply, replies, replied                    |
| **forward** (forwarded, forwarding): sharing a post or comment with a guild. A forwarded comment becomes its own post in the guild. | promote, yank, crosspost, "share to guild" |
| **repost**: putting someone's post or comment on your own profile (a different feature from forwarding).                            |                                            |

Guilds only ever receive forwards, so guild settings and errors talk about _forwarding_ (e.g. "Restrict forwarding", "disallows bots from forwarding and commenting").
External contracts keep their old names on purpose: the URL `/notifications/replies`, the JSON field `replies`, DB columns such as `restricted_posting` and `promoted_by_id` (Python attributes use the new names), the `/mod/kick/...` URLs, and legacy mod-log kinds (`yank_post`, `kick_post`).
`tests/test_terminology.py` enforces this; extend its allowlist only for a real external contract.

## Post options (legacy app)

- **Who can comment?** (`submissions.comment_permission`: 0 everyone, 1 accounts the author follows, 2 Premium accounts) is set by the author on the post on their own profile. The rule lives in `ruqqus/helpers/comment_permission.py`: `api_comment` enforces it (the author and admins are exempt) and `rendered_page` passes `comment_restriction` to the templates, which show a notice instead of the comment box. A forwarded copy never reads it: copies follow their guild's rules (`Board.can_comment`). Do not copy the column onto a forward.
- **Content disclosure** (`paid_partnership`, `made_with_ai` on `submissions` and `comments`) is set by the author when writing or editing a post or comment, copied onto forwards (post and comment forwards), and shown as labels from the `disclosure_badges` / `disclosure_row` macros. Read the form fields with `post_fields.flag()` (an absent field keeps the stored value; "false" means off). `tests/test_content_disclosure.py` keeps schema, models, routes, templates and JS agreeing.
- **Anonymous posts and comments** (`is_anonymous` on `submissions` and `comments`): the real `author_id` stays on the row (bans, spam, reports and admin tools need it) but only the author and site admins (`admin_level >= 3`) are told who it is. **Never read `.author` / `.author_id` to show who wrote something.** Use `ruqqus/helpers/anonymity.py`: in templates `author_of(item, v)` (the author object, or a stand-in "Anonymous"), `anon_hidden(item, v)` (hide anything that would single the author out: the Submitter mark, block / exile markers, tip, block and exile buttons) and `op_badge(...)`; in JSON `anonymity.identity_hidden(self, anonymity.current_viewer())`; in queries that list ONE author's items (profile tabs, `author:` search, Following) `anonymity.hide_anonymous(Model, v)`. The flag is set at creation only (never editable), copied onto forwards, forced on when you comment on your own anonymous post, and an anonymous item cannot be tipped, heralded, distinguished or reposted by its author. Anonymous posts do not bell-notify the author's followers, the region filter does not match them through their author, and `public_post_count` / `public_comment_count` leave them out. `tests/test_anonymity.py` and `tests/test_anonymity_assets.py` enforce this; a new place that shows an author needs the same treatment.
- **Formatting extras** in post and comment markdown (`{c:red}text{/c}`, `{h:blue}text{/h}`, `==text==`, `::: center ... :::`, `[Label](url){.button}`) only accept fixed names, never CSS. `ruqqus/helpers/post_formatting.py` is the single list (colours, highlights, alignments, button class): `helpers/markdown.py` renders them, `helpers/sanitize.py` keeps no other CSS class on `span`/`mark`/`div`/`a` (never allow `style` or a free `class`), and `tests/test_post_formatting.py` checks that `main.scss` and `main_dark.scss` have a rule for every class. To add an option: add it to that list, then a rule in both stylesheets.
- **Post editor toolbar** (create and edit a post; comments keep the small toolbar): `assets/js/post_editor.js` builds it for every `<div class="post-toolbar" data-target="<textarea id>">`. The text stays plain markdown and every edit goes through `editorReplace()` in `all_js.js` (keeps browser undo/redo). Write/Preview posts to `/api/preview` (same renderer and sanitizer as a real post); Template snippets are `post_templates` rows behind `/api/post_templates` (20 per user, see `routes/post_editor.py`). The image, audio and video buttons are disabled placeholders: an uploader calls `PostEditor.register('image', fn)` to switch one on. `tests/test_post_editor_assets.py` keeps the script's colour names, its CSS classes and the two forms in step.
- **Drafts and scheduled posts** (Save draft / Schedule / Drafts on Create a post): a draft is a `post_drafts` row (never a `submissions` row, which would leak into feeds and counts) with its fields in `options` JSON. Rules are in `helpers/post_drafts.py`, endpoints in `routes/post_drafts.py`, the UI in `assets/js/post_drafts.js`. A scheduled draft is published by `scripts/publish_scheduled.py` (its own supervisord program, polling every 30s): it submits through the real `POST /api/vue/submit` in-process as the author, from the IP and region saved with the draft, so every posting rule, the posting throttle and bans apply as for the Post button. **Never copy `submit_post`'s logic into the scheduler.** A new option on the composer needs: the field in `clean_fields` / `publish_form`, the composer markup, and `post_drafts.js`'s `collect()`. Run the publisher by hand with `PYTHONPATH=. python scripts/publish_scheduled.py --once`; uploaded images are not kept in drafts.
- **Feed composer** (top of the home / all feed and of a guild page, desktop only): `templates/partials/inline_composer.html` + `assets/js/inline_composer.js`. It posts through the real `POST /api/vue/submit` (every rule, throttle and ban applies), so a new composer option needs the same three places as the drafts: the field in the partial, `post_options.html` if it is shared, and `tests/test_inline_composer_assets.py`. On a guild page the hidden `forward_guilds` field makes it post on your profile and forward to that guild; the card added to the feed comes from `GET /inpage/post_card/<id>?guild=` (your own posts only).
- **Infinite scroll** (`assets/js/infinite_scroll.js`): every feed still pages 25 posts with a `Prev`/`Next` control; the script follows the enabled `Next` link of the page, fetches that page and lifts its post cards out, so no route needs a fragment mode. Keep each feed template's `ul.pagination` with a link labelled exactly `Next` and its post list in `.posts` (`tests/test_infinite_scroll_assets.py`). Behaviour of cards added after load is bound by `bindPostCards(root)` in `all_js.js`: a new card behaviour must be added there as well as at load.
- **Side panels** (desktop >= 992px): the navbar Notifications, Chat and Create post icons (`data-side-panel` in `default.html`) open `assets/js/side_panels.js`'s panel in place of the right sidebar and fold the left sidebar to its rail (with `classList`, never `toggle_sidebar_collapse()`, which also flips the saved preference). Each panel is a frame of the real page with `?embed=1`: `default.html` then adds `body.embedded` (no navbar or sidebars, `<base target="_top">`) and `assets/js/embedded.js` keeps tab bars and Prev/Next inside the frame. `/composer` is the Create post panel's page. `__main__.py` sends `X-Frame-Options: sameorigin` (not `deny`) only for `?embed=1` on `/notifications*`, `/chat` and `/composer`. Frames are kept (hidden) once opened so the chat keeps its Matrix connection. `header.html` (submit / settings pages) has its own navbar without the panel triggers. Chat shows the list OR the conversation below 768px (phones and the panel). `tests/test_side_panels_assets.py` keeps this in step.
- **Navbar alignment** (desktop >= 992px): the logo lines up with the left sidebar's content, the account section with the right sidebar's, and the search box is centred over the feed. `main.scss` / `main_dark.scss` hold the values for the normal 1326px block (sidebar 300 + feed 726 + sidebar 300, each side plus the 15px column gutter) and `assets/js/nav_align.js` replaces them with the real column edges through four CSS variables on `<html>` (`--nav-inset-left`, `--nav-inset-right`, `--nav-search-left`, `--nav-search-width`), so it also holds with the left sidebar folded to its rail or a side panel open. A new layout that changes a column's width needs no change as long as it uses `#sidebar-left`, the right `.sidebar` or `#side-panel`; changing the 300 / 726 / 300 widths means changing `BLOCK` in the script and the `1326px` in both stylesheets. `header.html`'s navbar (Create post, settings) gets the default alignment from the CSS alone. `tests/test_nav_align_assets.py`.
- **Chat client** (`assets/chat_src/src`, built into `assets/js/chat_bundle.js` by the Dockerfile; locally `npm install && npm run build` there): 1:1 Matrix messages with quotes (Matrix `m.in_reply_to`; the UI says "quote", never the banned word), reactions, edit, delete, typing and "Seen". The other person is addressed by Matrix id (`otherMxidOf`), never from the room member list (it also holds the bot that created the room). Read receipts and mark-read go out once per newest message: our own receipt redraws the thread, so doing it on every draw is a request loop that trips the site's rate limit. In encrypted rooms reactions and edits reach the appservice webhook as `m.room.encrypted`; only their `m.relates_to` is readable, and `helpers/chat_events.is_new_message` uses it so they are not counted as unread messages. Not built (roadmap): attachments and voice notes (needs a decision on encrypted media vs CSAM scanning), group DMs (`chat_conversations` is pair-only), 1:1 and group calls (coturn / LiveKit), audio Spaces. `tests/test_chat_events.py`.
- The option controls shared by the composer, the edit form and the comment forms live in `templates/partials/post_options.html`; the rules text in `templates/partials/posting_rules.html` (keep `help/rules.html` in step: `tests/test_posting_rules.py`).

## Word filter (legacy app)

Viewers browse at a level (`User.filter_level`: 0 Off, 1 Standard - the default and what logged-out visitors get, 2 Child). Content is never removed; it is hidden per viewer.

- Posts and comments get a stored rating when written (`word_severity`: 0 clean, 1 profanity, 2 extreme) from `ruqqus/helpers/wordfilter.py` (the matching engine, stdlib only) via `helpers/word_filter_store.py`. Standard hides 2; Child hides 1 and 2 plus anything `is_sensitive` and sensitive guilds.
- **Every new listing or page must go through `ruqqus/helpers/visibility.py`**: `filter_posts` / `filter_comments` / `filter_boards` / `filter_users` on queries, `post_hidden` / `comment_hidden` / `board_hidden` / `user_hidden` for one object, `hidden_notice(v)` for a direct link. Do not read `hide_offensive` or `is_offensive`.
- Memoized id-lists must include `filter_level=viewer_level(v)` in their arguments (it is the cache key). Caching is off on localhost, so this cannot be noticed in dev.
- Anything that writes a title, body, bio, description, username or guild name must re-stamp it (`apply_post_severity`, `apply_comment_severity`, `apply_user_severity`, `apply_board_severity`).
- The word list lives in `word_filter_entries` and is edited at `/admin/word_filter` (plain words, never regex); an empty table means the built-in list in `helpers/wordfilter_seed.py`. After changing the list, re-scan (admin button or `PYTHONPATH=. python scripts/rescan_word_filter.py`).
- Schema changes are manual: `scripts/migrations/*.sql` for existing databases, mirrored in `schema.sql`.

## Linked media storage (legacy app)

Ruqqus keeps no copy of an uploaded picture, sound or video. A member links an account once (`/settings/media`); the browser sends the file straight to it and a post only holds a reference. Start with `ruqqus/helpers/media/rules.py` (the rules, no I/O).

- **Two shapes of provider** (`helpers/media/base.py` is the interface). _Served_ (Google Drive, `gdrive.py`): plain storage that stays private in the member's account; Ruqqus fetches it and shows it at `/media/<id>/<token>.<ext>` (`routes/media.py`). _Hosting_ (YouTube, `youtube.py`): the site plays the file itself and the post links to it. `dev.py` is a stand-in served provider for local testing, only with `MEDIA_DEV_PROVIDER=1` (never on a live site).
- **Never serve Drive files by a Google link.** Google has no supported way to show a Drive file in an `<img>`, a shared file shows its owner's Google name (which would break pseudonymity and anonymous posts), the Drive API terms forbid using Drive as a CDN, and the Cloudflare CSAM scan only covers this site's own addresses.
- **Who sees a file** (`rules.access`): part of a live post or comment: public, `Cache-Control: public, max-age=31536000, immutable`, no session cookie (`g.skip_session_cookie`); not attached yet: its owner only, `no-store`; otherwise refused, so the site is not a free file host. Every place that saves a post's link or text, or a comment's text, calls `media.attach.sync(...)`, commits, then `media.safety.scan_later(...)`. Removing a post or comment calls `media.cdn.purge(...)`.
- **A served file is typed by its first bytes** (`rules.sniff`), sent with our own `Content-Type`, `nosniff` and a sandboxing CSP, and refused when the provider's checksum no longer matches the one recorded at upload (the owner can change the file in their storage). No SVG or HTML type, ever.
- **Uploading** (`assets/js/media_upload.js`): `POST /api/media/uploads` answers where the browser sends the bytes (a provider upload session the server opened; no credential ever reaches a browser), then `POST /api/media/uploads/<id>/complete` checks the file. Pictures are re-drawn in the browser first, which drops EXIF (location); never fall back to the original. A post's own picture travels as the `media` form field (an asset id), text pictures as `![](/media/...)`, sounds as `[audio](/media/...)`, which `helpers/sanitize.py` turns into an `<audio>` player (only for exactly this site's own sound address). No thumbnail is generated for linked media.
- **Google** (`google_oauth.py`): needs `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`; redirect `<site>/settings/media/google/callback`. `drive.file` is asked for first (not a sensitive scope: only files this site created). `youtube.upload` is asked for only when a member first adds a video; it is a sensitive scope (Google must verify the app) and the API project must pass YouTube's audit, or YouTube forces every upload private, which is reported as `restricted`. The refresh token is stored encrypted (`secret_box`). `AccountLost` (access taken away at Google) marks the account `error` without writing its files off; `ProviderDown` is temporary and never cached.
- A video from a member's own channel shows the channel name, so it is refused on an anonymous post (`media.attach.own_video`). Drive media shows no identity and is allowed.
- **To add a provider**: a module with a `Provider` subclass, an entry in `registry.PROVIDERS` / `ACCOUNT_OF`, its kinds in `rules.PROVIDERS` (and `rules.SERVED` if Ruqqus serves it). Posts, comments and the upload script do not change.
- Not on linked storage: avatars, banners and guild images (still the site's S3 bucket, `helpers/aws.py`), and anything uploaded before. When the site offers no linked storage, the old server-side image upload is what the composers use.
- The Google and YouTube code is tested with Google's side faked (`tests/test_media_google.py`, `tests/test_media_youtube.py`); it has not been run against the real services. The first thing to check with real credentials is that the browser's PUT to the upload session is allowed across origins (the session is opened with the site's `Origin`).
- Tests: `tests/test_media_*.py`.

## Rules

- Run the check command before declaring any task done.
- Write tests for auth, permissions, and voting logic. Legacy code has working auth/voting
  (`ruqqus/routes/votes.py`, `helpers/wrappers.py`); add characterization tests before changing it.
- Never commit secrets, and never disable lint/type rules to silence errors; fix root causes.
  The only existing suppressions are the two `# noqa: F821` in `ruqqus/helpers/aws.py` (known bug).
- `lib/` was once gitignored; `web/src/lib/` is now tracked. Do not re-add a blanket `lib/` ignore.
- UI work: follow the taste skill while building; open the page with Playwright (desktop and mobile
  widths) and check it visually before finishing.
- Use the Emil skill (`emil-design-eng`) when polishing animations and micro-interactions.
- Run the web-design-guidelines audit on changed UI files before committing.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:

- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
