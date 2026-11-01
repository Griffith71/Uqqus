import { test, expect, type Page } from "@playwright/test";
import { SITE_PATHS } from "./pages";

// Boxes that overlap or touch where they should not (the owner, 2026-10-30: "double check the whole site and make sure there aren't these overlaps now
// because of sizing changes", after an open filter panel's Apply button was left 2px from the next row):
//   npm run overlap                 the matrix below (about 10 minutes)
//   OVERLAP_FULL=1 npm run overlap  every page at 390, 768, 1280 and 1440 in both themes and at Extra large text (about an hour)
//   OVERLAP_REPORT=1 npm run overlap   list what it finds without failing, to triage a new page
// What it reads, in the browser, from the boxes the page really lays out:
//   overlap  two siblings (children of one parent, not floated or out of the flow) whose boxes cross by more than 1px on both axes
//   tight    two stacked siblings closer than 4px where one is a control (a button, a field, a link drawn as a button) and the other is not the
//            same kind: the Apply button 2px above the next row was exactly this
// and prints, without failing, a box that sticks out past the window and sideways scroll. What it leaves out, because each is a false alarm that was
// found by running it: a box clipped by a scrolling or overflow-hidden ancestor (a list of 32 rows in a 220px box), a closed <details>, absolutely
// positioned and fixed parts (menus, bars: they are placed, not stacked), the top bar, inline text, a hit area that is padding with a negative margin
// of the same size (the sidebar rows, "Who to follow" names), and what is in ALLOWED with its reason. A new entry there says why it is intended.

const FULL = process.env.OVERLAP_FULL === "1";
const REPORT = process.env.OVERLAP_REPORT === "1";

// [kind, a regular expression over "<first element> | <second element>" (tag#id.classes, no text), why it is meant]
const ALLOWED: [string, RegExp, string][] = [
  ["tight", /^\S*(arrow-(up|down)|score)\S* \| \S*(arrow-(up|down)|score)\S*$/, "the vote stack: two arrows and the score are one column, each arrow a padded hit area (_feed.scss)"],
  ["tight", /div\.ic-head.* \| textarea\.form-control\.ic-title/, "the feed composer's title field sits under its pill row; the field's own padding is the space the eye sees"],
  ["tight", /(p\.post-snippet \| button\.text-expand|div\.comment-clamp.* \| button\.text-expand-btn)/, "\"Show more\" is a text link drawn under the text it expands: a button with no box"],
  ["tight", /input\.form-control\.copy-link \| form$/, "the new-key button's own mt-2 collapses out through its <form>: the button is 7px under the field"],
  ["tight", /button\.btn\.btn-secondary\.btn-block \| button\.btn\.btn-transparent\.bell-button/, "the phone guild banner's Leave pill with the bell under it: the bell is a transparent 44px target (touch) and its 14px glyph sits about 15px clear of the pill"],
];

type Finding = { kind: "overlap" | "tight"; a: string; b: string; amount: number; parent: string; text: string };

async function scan(page: Page, root = "body"): Promise<{ findings: Finding[]; sideways: number; outside: string[] }> {
  return page.evaluate((rootSelector) => {
    const findings: any[] = [];
    const outside: string[] = [];
    const vw = document.documentElement.clientWidth;
    const rootEl = document.querySelector(rootSelector) as HTMLElement | null;
    if (!rootEl) return { findings, sideways: 0, outside };

    // a name for a box that is the same for every post and every guild: its classes without the ones that carry a number or an id, and its id
    // only when it has no class to be told by (and then with its digits and base-36 tail taken off)
    const sig = (e: Element) => {
      const cls = (e.getAttribute("class") || "").split(/\s+/).filter((c) => c && !/\d/.test(c)).slice(0, 3).join(".");
      return `${e.tagName.toLowerCase()}${cls ? "." + cls : e.id ? "#" + e.id.replace(/[-_][0-9a-z]+$/i, "").replace(/\d+/g, "") : ""}`;
    };
    const textOf = (e: Element) => (e.textContent || "").trim().replace(/\s+/g, " ").slice(0, 24);
    const isControl = (e: Element) => e.matches("button, input, select, textarea, .btn, [role=button]");
    // a caption or a hint that belongs to the field next to it sits close to it by design (a label above, a hint or an error under)
    const isCaption = (e: Element) => e.matches("label, legend, small, .form-text, .invalid-feedback, .text-danger, .text-muted, [class*=text-small]");

    // a box as the page really lays it out: clipped to every scrolling or overflow-hidden ancestor, and without the hit area a negative margin grows
    const boxOf = (e: Element, cs: CSSStyleDeclaration) => {
      const r = e.getBoundingClientRect();
      let l = r.left, t = r.top, rr = r.right, b = r.bottom;
      const ml = parseFloat(cs.marginLeft), mr = parseFloat(cs.marginRight), mt = parseFloat(cs.marginTop), mb = parseFloat(cs.marginBottom);
      if (ml < 0) l -= ml;
      if (mr < 0) rr += mr;
      if (mt < 0) t -= mt;
      if (mb < 0) b += mb;
      for (let a = e.parentElement; a && a !== document.documentElement; a = a.parentElement) {
        const s = getComputedStyle(a);
        const ox = s.overflowX !== "visible", oy = s.overflowY !== "visible";
        if (!ox && !oy) continue;
        const ar = a.getBoundingClientRect();
        if (ox) { l = Math.max(l, ar.left); rr = Math.min(rr, ar.right); }
        if (oy) { t = Math.max(t, ar.top); b = Math.min(b, ar.bottom); }
      }
      return { l, t, r: rr, b };
    };

    const walk = (parent: Element) => {
      const kids: { e: Element; cs: CSSStyleDeclaration; box: { l: number; t: number; r: number; b: number } }[] = [];
      for (const e of Array.from(parent.children)) {
        if (/^(SCRIPT|STYLE|TEMPLATE|NOSCRIPT|LINK|META|svg|path)$/i.test(e.tagName) || e.closest("svg")) continue;
        const cs = getComputedStyle(e);
        if (cs.display === "none" || cs.display === "contents" || cs.visibility === "hidden") continue;
        if (cs.position === "absolute" || cs.position === "fixed") continue;                       // placed, not stacked (and so are its children)
        if (e.id === "navbar" || e.matches(".dropdown-menu, .tooltip, .toast, .modal-backdrop")) continue;
        const r = e.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (cs.float !== "none") continue;
        const box = boxOf(e, cs);
        if (box.r > vw + 2 && !e.closest("[class*=scroll], .explore-chips, .nav-tabs, .tab-scroll")) outside.push(`${sig(e)} ${Math.round(box.r - vw)}px past the window`);
        if (cs.display !== "inline") kids.push({ e, cs, box });
      }
      // overlap and tight pairs among the siblings
      for (let i = 0; i < kids.length; i++) {
        for (let j = i + 1; j < kids.length; j++) {
          const A = kids[i], B = kids[j];
          const ix = Math.min(A.box.r, B.box.r) - Math.max(A.box.l, B.box.l);
          const iy = Math.min(A.box.b, B.box.b) - Math.max(A.box.t, B.box.t);
          if (ix <= 1 || (iy <= 0 && ix <= 1)) continue;
          if (iy > 1 && ix > 1) {
            findings.push({ kind: "overlap", a: sig(A.e), b: sig(B.e), amount: Math.round(Math.min(ix, iy) * 10) / 10, parent: sig(parent), text: `${textOf(A.e)} | ${textOf(B.e)}` });
            continue;
          }
          // stacked: one above the other, sharing a good part of their width
          const upper = A.box.t <= B.box.t ? A : B, lower = upper === A ? B : A;
          const gap = lower.box.t - upper.box.b;
          const share = ix / Math.max(1, Math.min(upper.box.r - upper.box.l, lower.box.r - lower.box.l));
          if (gap >= -1 && gap < 4 && share > 0.4 && (isControl(upper.e) || isControl(lower.e)) && sig(upper.e) !== sig(lower.e)
              && !(isCaption(upper.e) || isCaption(lower.e))) {
            if (parent.matches(".btn-group, .input-group, .btn-toolbar, .nav, .nav-tabs, .pagination, .list-group, .modal-footer, .dropdown-menu")) continue;
            findings.push({ kind: "tight", a: sig(upper.e), b: sig(lower.e), amount: Math.round(gap * 10) / 10, parent: sig(parent), text: `${textOf(upper.e)} | ${textOf(lower.e)}` });
          }
        }
      }
      for (const k of kids) {
        if (k.e.tagName === "DETAILS" && !(k.e as HTMLDetailsElement).open) {
          const summary = k.e.querySelector(":scope > summary");
          if (summary) walk(summary);
          continue;
        }
        walk(k.e);
      }
    };
    walk(rootEl);

    // an open panel (a Bootstrap .collapse.show) and what follows it: its last control is not a sibling of the next row, so the pass above
    // cannot see them side by side. The Apply button was 2px from the next filter row; 6px is the least that reads as a gap
    for (const panel of Array.from(rootEl.querySelectorAll(".collapse.show"))) {
      const controls = panel.querySelectorAll("button, input, select, .btn");
      const last = controls[controls.length - 1];
      const next = panel.parentElement && panel.parentElement.nextElementSibling;
      if (!last || !next) continue;
      const target = (next.querySelector(":scope > a, :scope > button") || next) as Element;
      const gap = Math.round((target.getBoundingClientRect().top - last.getBoundingClientRect().bottom) * 10) / 10;
      if (gap < 6) findings.push({ kind: "tight", a: `${sig(last)} (the last control of an open panel)`, b: sig(target), amount: gap, parent: sig(panel), text: `${textOf(last)} | ${textOf(target)}` });
    }
    return { findings, sideways: Math.round(document.documentElement.scrollWidth - vw), outside: [...new Set(outside)].slice(0, 6) };
  }, root);
}

const allowed = (f: Finding) => ALLOWED.find(([kind, re]) => kind === f.kind && re.test(`${f.a} | ${f.b}`));

async function go(page: Page, path: string) {
  for (let attempt = 0; attempt < 5; attempt++) {                                    // the site's rate limit answers a burst with a bare page
    await page.goto(path, { waitUntil: "load" });
    if ((await page.locator("#navbar").count()) > 0) break;
    await page.waitForTimeout(3000 * (attempt + 1));
  }
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(2200);                                                   // the sidebar's boxes load after the page
}

type Setup = { label: string; width: number; scheme: "light" | "dark"; cookies: { name: string; value: string }[]; touch?: boolean };
const SETUPS: Setup[] = FULL
  ? [390, 768, 1280, 1440].flatMap((width) => (["light", "dark"] as const).flatMap((scheme) => [
      { label: `${width} ${scheme}`, width, scheme, cookies: [], touch: width === 390 },
      { label: `${width} ${scheme} xlarge`, width, scheme, cookies: [{ name: "text_size", value: "xlarge" }], touch: width === 390 },
    ]))
  : [
      { label: "390 light", width: 390, scheme: "light", cookies: [], touch: true },
      { label: "768 light", width: 768, scheme: "light", cookies: [] },
      { label: "1280 light", width: 1280, scheme: "light", cookies: [] },
      { label: "1280 dark", width: 1280, scheme: "dark", cookies: [] },
      { label: "1280 light xlarge", width: 1280, scheme: "light", cookies: [{ name: "text_size", value: "xlarge" }] },
    ];

// every distinct finding once, with where it was seen
function summarise(seen: Map<string, { f: Finding; where: Set<string> }>) {
  return [...seen.values()].map(({ f, where }) => `${f.kind.padEnd(7)} ${String(f.amount).padStart(5)}px  ${f.a}  |  ${f.b}  (in ${f.parent})  "${f.text}"  — ${[...where].slice(0, 4).join(", ")}${where.size > 4 ? ` +${where.size - 4}` : ""}`);
}

for (const setup of SETUPS) {
  test(`no boxes overlap or crowd a control: every page at ${setup.label}`, async ({ browser }) => {
    test.setTimeout(1_800_000);
    const context = await browser.newContext({ storageState: ".auth/viewer.json", viewport: { width: setup.width, height: 1400 }, colorScheme: setup.scheme, ...(setup.touch ? { hasTouch: true, isMobile: true } : {}) });
    if (setup.cookies.length) await context.addCookies(setup.cookies.map((c) => ({ ...c, url: "http://localhost" })));
    const seen = new Map<string, { f: Finding; where: Set<string> }>();
    const warnings: string[] = [];
    const note = (path: string, found: { findings: Finding[]; sideways: number; outside: string[] }) => {
      for (const f of found.findings) {
        if (allowed(f)) continue;
        const key = `${f.kind}|${f.a}|${f.b}|${f.parent}`;
        const hit = seen.get(key) ?? { f, where: new Set<string>() };
        hit.where.add(path);
        seen.set(key, hit);
      }
      if (found.sideways > 2) warnings.push(`${path}: scrolls sideways by ${found.sideways}px`);
      for (const o of found.outside) warnings.push(`${path}: ${o}`);
    };
    const page = await context.newPage();
    for (const path of SITE_PATHS) {
      await go(page, path);
      note(path, await scan(page));
    }
    // states that only exist once something is opened
    await go(page, "/all");
    if (setup.width >= 992) {
      for (const id of ["regional", "language", "categorical", "post_type"]) {
        await page.click(`#sidebar-left a[data-target="#sidebar-filter-${id}"]`);
        await page.waitForTimeout(500);
      }
      note("/all (filter panels open)", await scan(page, "#sidebar-left"));
    } else {
      for (const [id, name] of [["regionalFilterModal", "regional"], ["languageFilterModal", "language"], ["categoricalFilterModal", "categorical"], ["postTypeFilterModal", "post_type"], ["advancedFilterModal", "advanced"]]) {
        await page.evaluate((m) => (window as any).jQuery(`#${m}`).modal("show"), id);
        await page.waitForTimeout(700);
        note(`/all (${name} sheet)`, await scan(page, `#${id} .modal-content`));
        await page.evaluate((m) => (window as any).jQuery(`#${m}`).modal("hide"), id);
        await page.waitForTimeout(500);
      }
    }
    await go(page, "/submit");
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => ((d as HTMLDetailsElement).open = true)));
    await page.waitForTimeout(400);
    note("/submit (details open)", await scan(page));
    await go(page, "/all");
    if (await page.locator("#dropdownMenuLink").count()) {
      await page.locator("#dropdownMenuLink").click();
      await page.waitForTimeout(600);
      note("/all (account menu open)", await scan(page, "#accountDropdownMenu"));
    }
    const lines = summarise(seen);
    if (warnings.length) console.log(`\n[${setup.label}] warnings (not failing):\n  ` + [...new Set(warnings)].join("\n  "));
    if (lines.length) console.log(`\n[${setup.label}] ${lines.length} finding(s):\n  ` + lines.join("\n  "));
    if (!REPORT) expect(lines, `overlapping or crowded boxes at ${setup.label}`).toEqual([]);
    await context.close();
  });
}
