import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { PAGES, open } from "./pages";

// The interface in every ready language (the owner, 2026-10-25: "some languages work, some don't"; translations long
// enough to break the design). For each language: switch it the way people do (a visitor with the footer switcher, the
// signed-in viewer with Settings > Content), check <html lang dir>, then measure every piece of text on the main pages
// against the English render of the same page and report what a translation pushed out of shape.
//
//   I18N_LABEL=before npm run i18n     ->  reports/i18n-<label>/<lang>.json and summary.json
//   I18N_LANGS=fr,de  I18N_PAGES=all,profile  I18N_WIDTHS=390,1280  narrow a run
const label = process.env.I18N_LABEL ?? "current";
const widths = (process.env.I18N_WIDTHS ?? "390,1280").split(",").map(Number);
const onlyPages = process.env.I18N_PAGES?.split(",");
const onlyLangs = process.env.I18N_LANGS?.split(",");
const out = `reports/i18n-${label}`;
const targets = PAGES.filter((t) => !onlyPages || onlyPages.includes(t.name));

test.describe.configure({ mode: "serial" });      // the viewer's language lives in the database: one at a time

type Item = { text: string; w: number; h: number; lh: number; clipped: boolean; outside: boolean };
type Shot = { scroll: boolean; items: Record<string, Item> };

// every visible element with text of its own, by a path that is the same in every language (the markup is)
function measure(): Shot {
  const items: Record<string, Item> = {};
  const path = (el: Element): string => {
    const parts: string[] = [];
    for (let node: Element | null = el; node && node !== document.body; node = node.parentElement) {
      const index = node.parentElement ? Array.from(node.parentElement.children).indexOf(node) + 1 : 1;
      parts.unshift(`${node.tagName.toLowerCase()}${node.id ? "#" + node.id : ""}:${index}`);
    }
    return parts.join(">");
  };
  for (const el of Array.from(document.body.querySelectorAll("*"))) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "svg", "OPTION"].includes(el.tagName)) continue;
    if (el.closest(".language-switcher")) continue;    // each language's own name: the same in every interface
    const own = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent ?? "").join("").trim();
    if (!/\p{L}/u.test(own)) continue;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (!rect.width || !rect.height || style.visibility === "hidden" || style.display === "none") continue;
    const parent = el.parentElement?.getBoundingClientRect();
    const fontSize = parseFloat(style.fontSize) || 16;
    const lh = style.lineHeight === "normal" ? fontSize * 1.2 : parseFloat(style.lineHeight) || fontSize * 1.2;
    const html = el as HTMLElement;
    const boxX = el.parentElement ? getComputedStyle(el.parentElement).overflowX : "visible";
    const beyond = !!parent && parent.width > 0 && (rect.right > parent.right + 2 || rect.left < parent.left - 2);
    const hides = ["hidden", "clip"].includes(style.overflowX) || style.textOverflow === "ellipsis";
    items[path(el)] = {
      text: own.replace(/\s+/g, " ").slice(0, 120),
      w: Math.round(rect.width), h: Math.round(rect.height), lh: Math.round(lh),
      // cut off by itself, or by a box around it that hides what doesn't fit (a sidebar row's value)
      clipped: (hides && html.scrollWidth > html.clientWidth + 1) || (beyond && ["hidden", "clip"].includes(boxX)),
      // outside its box, unless the box scrolls sideways by design (Explore's topic chips) or cuts it (above)
      outside: beyond && !["auto", "scroll", "hidden", "clip"].includes(boxX),
    };
  }
  return { scroll: document.documentElement.scrollWidth > window.innerWidth + 1, items };
}

async function shoot(page: Page, path: string): Promise<Shot> {
  await open(page, path);
  return page.evaluate(measure);
}

function compare(en: Shot, shot: Shot) {
  const found: { problem: string; key: string; english: string; text: string; w: number; h: number }[] = [];
  if (shot.scroll && !en.scroll) found.push({ problem: "page scrolls sideways", key: "", english: "", text: "", w: 0, h: 0 });
  for (const [key, item] of Object.entries(shot.items)) {
    const was = en.items[key];
    if (!was || was.text === item.text) continue;           // member text, or the same words: not a translation's doing
    const row = { key, english: was.text, text: item.text, w: item.w, h: item.h };
    const label = was.text.length <= 40;                     // a label, not a sentence (sentences wrap by design)
    if (item.clipped && !was.clipped) found.push({ problem: "cut off", ...row });
    if (item.outside && !was.outside) found.push({ problem: "sticks out of its box", ...row });
    if (label && was.h <= was.lh * 1.6 && item.h > Math.max(was.h * 1.4, was.h + item.lh * 0.6))
      found.push({ problem: "wraps onto another line", ...row });
    // a short label much wider than the design drew it ("0 Rep" -> "0 de réputation" in the navbar)
    else if (was.text.length <= 25 && item.w > was.w * 1.5 && item.w - was.w >= 24)
      found.push({ problem: "much wider than in English", ...row });
  }
  return found;
}

// the share of a page's words still exactly as in English (the settings and sign-in pages: little member text)
function englishLeft(en: Shot, shot: Shot) {
  let words = 0, same = 0;
  for (const [key, item] of Object.entries(shot.items)) {
    const was = en.items[key];
    if (!was || !/\p{L}{3}/u.test(was.text)) continue;
    words++;
    if (was.text === item.text) same++;
  }
  return words ? Math.round((100 * same) / words) : 0;
}

async function readyLanguages(page: Page) {
  await open(page, "/");
  const hrefs = await page.$$eval(".language-switcher-list a", (links) => links.map((a) => (a as HTMLAnchorElement).href));
  return hrefs.map((h) => new URL(h).searchParams.get("lang") ?? "").filter((l) => l && l !== "en");
}

async function setVisitor(page: Page, lang: string) {
  await page.goto(`/interface_language?lang=${lang}&next=/`, { waitUntil: "load" });
}

async function setMember(page: Page, lang: string) {
  await open(page, "/settings/content");
  await page.selectOption("#my-language-0", lang);
  await Promise.all([page.waitForURL("**/settings/content**"),
    page.click('form[action="/settings/languages"] button[type="submit"]')]);
}

test("every ready language switches and keeps the design's shape", async ({ browser }) => {
  test.setTimeout(6 * 60 * 60_000);
  mkdirSync(out, { recursive: true });
  const visitor = await (await browser.newContext()).newPage();
  const member = await (await browser.newContext({ storageState: ".auth/viewer.json" })).newPage();
  const langs = (await readyLanguages(visitor)).filter((l) => !onlyLangs || onlyLangs.includes(l));
  const pageFor = (signedIn?: boolean) => (signedIn ? member : visitor);

  // English first: the shape every language is measured against (kept, so a run per language can reuse it)
  const baselineFile = `${out}/en.json`;
  let english: Record<string, Shot> = existsSync(baselineFile) ? JSON.parse(readFileSync(baselineFile, "utf8")) : {};
  if (!Object.keys(english).length) {
    await setVisitor(visitor, "en");
    await setMember(member, "en");
    for (const t of targets) for (const width of widths) {
      const page = pageFor(t.signedIn);
      await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
      english[`${t.name}@${width}`] = await shoot(page, t.path);
    }
    writeFileSync(baselineFile, JSON.stringify(english));
  }

  const summary: Record<string, unknown>[] = [];
  for (const lang of langs) {
    await setVisitor(visitor, lang);
    await setMember(member, lang);
    const switched = {
      visitor: await visitor.evaluate(() => document.documentElement.lang),
      member: await member.evaluate(() => document.documentElement.lang),
      dir: await member.evaluate(() => document.documentElement.dir),
    };
    const problems: Record<string, unknown>[] = [];
    const left: Record<string, number> = {};
    for (const t of targets) for (const width of widths) {
      const page = pageFor(t.signedIn);
      await page.setViewportSize({ width, height: width < 768 ? 844 : 900 });
      const shot = await shoot(page, t.path);
      const en = english[`${t.name}@${width}`];
      if (!en) continue;
      for (const p of compare(en, shot)) problems.push({ page: t.name, width, ...p });
      if (["settings", "login"].includes(t.name) && width === widths[widths.length - 1]) left[t.name] = englishLeft(en, shot);
    }
    writeFileSync(`${out}/${lang}.json`, JSON.stringify({ lang, switched, englishLeft: left, problems }, null, 1));
    summary.push({ lang, ...switched, englishLeft: left, problems: problems.length });
    console.log(`${lang}: <html lang> visitor ${switched.visitor}, member ${switched.member} ${switched.dir}; ` +
      `English left ${JSON.stringify(left)}; ${problems.length} layout problems`);
  }
  await setMember(member, "en");                      // the viewer back in English for the other checks
  writeFileSync(`${out}/summary.json`, JSON.stringify(summary, null, 1));
  for (const row of summary) {
    expect.soft(String(row.visitor).split("-")[0], `${row.lang}: the footer switcher`).toBe(String(row.lang).split("-")[0]);
  }
});
