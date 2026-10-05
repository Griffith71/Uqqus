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

| Say | Never say |
| --- | --- |
| **post**: always created first on the author's own profile. You cannot post to a guild. | "post to/in a guild", "submit to a guild" |
| **comment**: anything that is a reply, whether on a post or on another comment. | reply, replies, replied |
| **forward** (forwarded, forwarding): sharing a post or comment with a guild. A forwarded comment becomes its own post in the guild. | promote, yank, crosspost, "share to guild" |
| **repost**: putting someone's post or comment on your own profile (a different feature from forwarding). | |

Guilds only ever receive forwards, so guild settings and errors talk about *forwarding* (e.g. "Restrict forwarding", "disallows bots from forwarding and commenting").
External contracts keep their old names on purpose: the URL `/notifications/replies`, the JSON field `replies`, DB columns such as `restricted_posting` and `promoted_by_id` (Python attributes use the new names), the `/mod/kick/...` URLs, and legacy mod-log kinds (`yank_post`, `kick_post`).
`tests/test_terminology.py` enforces this; extend its allowlist only for a real external contract.

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
