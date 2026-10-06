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
- **Formatting extras** in post and comment markdown (`{c:red}text{/c}`, `{h:blue}text{/h}`, `==text==`, `::: center ... :::`, `[Label](url){.button}`) only accept fixed names, never CSS. `ruqqus/helpers/post_formatting.py` is the single list (colours, highlights, alignments, button class): `helpers/markdown.py` renders them, `helpers/sanitize.py` keeps no other CSS class on `span`/`mark`/`div`/`a` (never allow `style` or a free `class`), and `tests/test_post_formatting.py` checks that `main.scss` and `main_dark.scss` have a rule for every class. To add an option: add it to that list, then a rule in both stylesheets.
- The option controls shared by the composer, the edit form and the comment forms live in `templates/partials/post_options.html`; the rules text in `templates/partials/posting_rules.html` (keep `help/rules.html` in step: `tests/test_posting_rules.py`).

## Word filter (legacy app)

Viewers browse at a level (`User.filter_level`: 0 Off, 1 Standard - the default and what logged-out visitors get, 2 Child). Content is never removed; it is hidden per viewer.

- Posts and comments get a stored rating when written (`word_severity`: 0 clean, 1 profanity, 2 extreme) from `ruqqus/helpers/wordfilter.py` (the matching engine, stdlib only) via `helpers/word_filter_store.py`. Standard hides 2; Child hides 1 and 2 plus anything `is_sensitive` and sensitive guilds.
- **Every new listing or page must go through `ruqqus/helpers/visibility.py`**: `filter_posts` / `filter_comments` / `filter_boards` / `filter_users` on queries, `post_hidden` / `comment_hidden` / `board_hidden` / `user_hidden` for one object, `hidden_notice(v)` for a direct link. Do not read `hide_offensive` or `is_offensive`.
- Memoized id-lists must include `filter_level=viewer_level(v)` in their arguments (it is the cache key). Caching is off on localhost, so this cannot be noticed in dev.
- Anything that writes a title, body, bio, description, username or guild name must re-stamp it (`apply_post_severity`, `apply_comment_severity`, `apply_user_severity`, `apply_board_severity`).
- The word list lives in `word_filter_entries` and is edited at `/admin/word_filter` (plain words, never regex); an empty table means the built-in list in `helpers/wordfilter_seed.py`. After changing the list, re-scan (admin button or `PYTHONPATH=. python scripts/rescan_word_filter.py`).
- Schema changes are manual: `scripts/migrations/*.sql` for existing databases, mirrored in `schema.sql`.

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
