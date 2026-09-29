@AGENTS.md

# Ruqqus (web)

A Reddit-like platform: communities, posts, nested comments, voting, moderation.
This is the `web/` app — a from-scratch Next.js rewrite living alongside the
original Flask app at the repo root. They are independent; do not mix imports
or config between them.

## Stack, and why

- **Next.js (App Router, TypeScript, src/)** — file-based routing, server
  components, and API routes in one framework.
- **Tailwind CSS + shadcn/ui** — utility CSS with accessible, unstyled-by-default
  Radix-based components we own the source of (button, input, textarea, card,
  dialog, dropdown-menu, avatar, skeleton, sonner).
- **Prisma (6.x) + PostgreSQL (Supabase)** — typed schema and migrations.
  Pinned to the 6.x line deliberately: Prisma 7 removed the `directUrl`
  datasource split that Supabase's pooled (PgBouncer)/direct connection setup
  needs. `DATABASE_URL` is the pooled runtime connection; `DIRECT_URL` is the
  direct connection Migrate uses.
- **Zod** — runtime validation for all server input (see rules below).
- **TanStack Query** — client-side server-state caching; `QueryProvider` is
  wired into the root layout.
- **Auth.js v5 (next-auth) + Prisma adapter** — session/account/user
  persistence via Prisma; GitHub and Google are placeholder providers until
  real OAuth apps exist.
- **react-markdown + remark-gfm + rehype-sanitize** — GFM markdown rendering
  for posts/comments that always sanitizes output before it hits the DOM.
- **@upstash/redis + @upstash/ratelimit** — serverless-friendly rate limiting
  for write endpoints (see `src/lib/ratelimit.ts`).
- **sharp** — image processing (avatars, thumbnails) in Node runtime routes.
- **Vitest + React Testing Library** — unit/component tests.
- **Playwright (Chromium)** — end-to-end tests against a built app.
- **Sentry (@sentry/nextjs)** — error monitoring. Package only for now; the
  setup wizard (DSN wiring, instrumentation files) hasn't been run yet.
- **Prettier + prettier-plugin-tailwindcss** — formatting, with class lists
  sorted automatically.

## Folder structure and naming

- `src/app/` — routes (App Router). Route groups/segments in kebab-case.
- `src/app/api/**/route.ts` — API route handlers.
- `src/components/ui/` — shadcn primitives (generated; prefer `shadcn add`
  over hand-editing when possible).
- `src/components/providers/` — app-wide client providers (e.g. `QueryProvider`).
- `src/components/**/__tests__/` — colocated component tests.
- `src/lib/` — framework-agnostic helpers: `db.ts` (Prisma client singleton),
  `auth.ts` (Auth.js config), `ratelimit.ts` (rate limiter factory).
- `src/lib/validators/` — Zod schemas, one file per domain, re-exported from
  `index.ts`.
- `src/types/` — ambient `.d.ts` module augmentations (e.g. `next-auth.d.ts`).
  Keep these out of `src/lib/` — a `.d.ts` sharing a basename with a sibling
  `.ts` file is silently ignored by `tsc`.
- `prisma/schema.prisma` — schema; `prisma/migrations/` — generated migrations.
- `e2e/` — Playwright specs.
- Components: PascalCase filenames for components, camelCase for functions/vars,
  kebab-case for route segments and non-component files.

## npm scripts

| Script | When to run it |
| --- | --- |
| `npm run dev` | Local development server. |
| `npm run build` | Production build; also what Playwright's `webServer` runs. |
| `npm run start` | Serve a production build (after `build`). |
| `npm run lint` | ESLint. Run before finishing any task. |
| `npm run typecheck` | `tsc --noEmit`. Run before finishing any task. |
| `npm run test` | Vitest unit/component tests. Run before finishing any task. |
| `npm run test:e2e` | Playwright end-to-end tests (builds + serves the app first). |
| `npm run db:migrate` | Create/apply a Prisma migration in dev (`prisma migrate dev`). |
| `npm run db:studio` | Open Prisma Studio to inspect the database. |

## Rules

- Validate every server input (API routes, server actions) with Zod. Define
  the schema in `src/lib/validators/`, not inline.
- Never write custom auth or password handling — everything goes through
  Auth.js and its providers/adapter.
- Always sanitize user-generated markdown (`rehype-sanitize`) before
  rendering; never `dangerouslySetInnerHTML` raw user content.
- Rate-limit every write endpoint using `createRateLimiter` from
  `src/lib/ratelimit.ts`.
- Write tests alongside every feature — unit/component tests colocated in
  `__tests__/`, e2e specs in `e2e/` for user-facing flows.
- Before finishing any task: run `npm run typecheck`, `npm run lint`, and
  `npm run test` (and `npm run build` for anything touching routing/config),
  and fix anything that fails.

## Roadmap

1. Schema and auth (this phase — foundation only, no domain models yet)
2. Communities and posts
3. Nested comments
4. Voting and ranking
5. Moderation
6. Search and notifications

