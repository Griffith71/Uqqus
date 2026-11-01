import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";

const ids: Record<string, string> = JSON.parse(readFileSync(new URL("./.fixtures.json", import.meta.url), "utf8"));

// `mask`: parts that change on every load (the sign-in page's random photo), painted over in the shots
// `density`: the reader's Comfortable / Compact choice, as the `density` cookie (partials/theme.html html_attrs)
export type Target = { name: string; path: string; signedIn?: boolean; mask?: string[]; density?: "compact" };

// What is photographed: the main screens, plus the seeded stress cases (scripts/seed_ui_fixtures.py).
export const PAGES: Target[] = [
  { name: "feed-visitor", path: "/" },
  { name: "feed-member", path: "/", signedIn: true },
  { name: "all", path: "/all" },
  { name: "all-compact", path: "/all", density: "compact" },
  { name: "post-thread", path: `/post/${ids.thread}`, signedIn: true },
  { name: "post-long-title", path: `/post/${ids.long}` },
  { name: "post-rtl", path: `/post/${ids.rtl}` },
  // a post in a guild: Post Info, then the guild's own sidebar (a profile post shows its author's About box)
  ...(ids.guild_post ? [{ name: "guild-post", path: `/post/${ids.guild_post}`, signedIn: true }] : []),
  { name: "profile", path: "/@ui_alice" },
  { name: "profile-long-name", path: "/@ui_averyverylongusername" },
  { name: "guild", path: "/+ui_photography", signedIn: true },
  { name: "guild-dark-colour", path: "/+ui_midnight" },
  { name: "guild-empty", path: "/+ui_empty" },
  { name: "explore", path: "/browse" },
  { name: "search-empty", path: "/search?q=nothingmatchesthisatall" },
  { name: "login", path: "/login", mask: [".splash-img"] },
  { name: "settings", path: "/settings/profile", signedIn: true },
  // the pages without a right sidebar: lists centred at the feed's width, Circles and the Studio as grids
  { name: "inbox", path: "/notifications", signedIn: true },
  { name: "history", path: "/history", signedIn: true },
  { name: "circles", path: "/circles", signedIn: true },
  { name: "studio", path: "/studio", signedIn: true },
  // What's happening (routes/happening_pages.py): the page's four tabs; the sidebar box is on the feeds above
  { name: "happening", path: "/happening" },
  { name: "happening-live", path: "/happening/live" },
  { name: "happening-calendar", path: "/happening/calendar?month=2026-11" },
  // the pages about one account that are not its profile read as its profile in the sidebar: Similar accounts, no Who to follow
  // search results with their tab bar: each kind (helpers/search_tabs.py), and a guild's own search with its two tabs
  { name: "search-posts", path: "/search?q=film" },
  { name: "search-comments", path: "/search?q=film&type=comments" },
  { name: "search-guilds", path: "/search?q=ui&type=guilds" },
  { name: "search-accounts", path: "/search?q=ui_&type=accounts", signedIn: true },
  { name: "search-curations", path: "/search?q=news&type=curations" },
  { name: "search-guild-scope", path: "/+ui_photography/search?q=film" },
  { name: "follow-list", path: "/@ui_alice/followers", signedIn: true },
  { name: "channels", path: "/@ui_alice/channels", signedIn: true },
];

// The member's pages the layout checks (type_scale.spec.ts, overlap.spec.ts) walk: the feeds, a profile, a guild and its settings, a post, Explore and
// its tabs, notifications, history, Create a post, Circles, the Studio, settings, help, trending, search, the deletion log, a curation, a follow list.
export const SITE_PATHS = ["/all", "/for_you", "/following", "/@ui_alice", "/+ui_photography", "/post/15", "/browse", "/curations", "/spaces", "/happening", "/notifications",
  "/history", "/submit", "/circles", "/studio", "/settings/profile", "/settings/content", "/settings/security", "/help/rules", "/help/bots",
  "/+ui_photography/mod/settings", "/trending", "/search?q=cats", "/log", "/&uisbtheirs0", "/@ui_alice/followers"];

export const WIDTHS = [390, 768, 1280, 1440];
export const THEMES = ["light", "dark"] as const;

export async function open(page: Page, path: string) {
  // a page the server dropped (its workers restarting while a long run goes on) is asked for again, three times
  for (let attempt = 1; ; attempt++) {
    try {
      await page.goto(path, { waitUntil: "load" });
      break;
    } catch (error) {
      if (attempt >= 3) throw error;
      await page.waitForTimeout(5000);
    }
  }
  // late boxes (Trending, Who to follow) and fonts settle; nothing on these pages animates for longer
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);
}
