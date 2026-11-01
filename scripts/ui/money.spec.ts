import { test, expect, type BrowserContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { open } from "./pages";

// The admins' money page (CLAUDE.md "Coins and money"; helpers/money_stats.py, templates/admin/money.html). It signs in as ui_traffic_admin:
//   docker compose exec -T ruqqus env PYTHONPATH=. /opt/ruqqus/service/venv/bin/python scripts/ui/seed_traffic.py     first
//   npm run money
// At 390 and 1280px in both themes and in each of the ad status's three states: every figure is drawn and none reads None, no sideways
// scroll and nothing off the screen, the state expected and no id, the link to AdSense is there (never followed), and axe finds nothing serious.
// The dev site has no AdSense settings, so it reads Off. For the other two a second copy of the app is started inside the dev container with a
// made-up client id and slots (ADSENSE_* are read from the environment), the page is read from it as this admin, and the browser is given
// that answer at the dev site's own address, so the stylesheets and fonts are the dev site's. The copy is stopped at the end.

const CONTAINER = process.env.RUQQUS_CONTAINER ?? "ruqqus-ruqqus-1";
const PY = "/opt/ruqqus/service/venv/bin/python";
const PORT = 8091;
const CLIENT = "ca-pub-0000000000000000";
const DESKTOP = { viewport: { width: 1280, height: 900 } } as const;
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;
const STATES: Record<string, string[]> = {
  off: [],
  client_only: [`ADSENSE_CLIENT=${CLIENT}`],
  live: [`ADSENSE_CLIENT=${CLIENT}`, "ADSENSE_SLOT_FEED=1111111111", "ADSENSE_SLOT_POST=2222222222"],
};

test.describe.configure({ mode: "serial" });

let state: Awaited<ReturnType<BrowserContext["storageState"]>>;

function stopCopy() {
  // waits for it to go: the old copy still answers on the port for a moment, and the next state would be read from it
  execSync(`docker exec ${CONTAINER} sh -c 'if test -f /tmp/money_copy.pid; then p=$(cat /tmp/money_copy.pid); kill $p; i=0; while kill -0 $p 2>/dev/null && [ $i -lt 50 ]; do sleep 0.2; i=$((i+1)); done; fi; rm -f /tmp/money_copy.pid; true'`);
}

// the page as a copy of the app with these settings answers it, for the signed-in admin
function pageFrom(settings: string[]): string {
  stopCopy();
  const env = settings.map((s) => `-e ${s}`).join(" ");
  execSync(`docker exec -d ${env} -w /opt/ruqqus/service ${CONTAINER} venv/bin/gunicorn ruqqus.__main__:app -w 1 -k gevent --bind 127.0.0.1:${PORT} --pid /tmp/money_copy.pid`);
  const cookie = state.cookies.find((c) => c.name === "session_ruqqus")!.value;
  const read = `
import time, urllib.request as u
request = u.Request("http://127.0.0.1:${PORT}/admin/money", headers={"Host": "localhost", "Cookie": "session_ruqqus=${cookie}"})
for attempt in range(60):
    try:
        print(u.urlopen(request, timeout=10).read().decode())
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("the copy never answered")
`;
  return execSync(`docker exec -i ${CONTAINER} ${PY} -`, { input: read, maxBuffer: 16_000_000 }).toString();
}

test.beforeAll(async ({ browser }) => {
  const first = await browser.newContext(DESKTOP);
  const form = await first.newPage();
  await open(form, "/login");
  await form.fill("#username", "ui_traffic_admin");
  await form.fill("#password", "ui-Password-1");
  await form.click("#login_button");
  await form.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15_000 });
  state = await first.storageState();
  await first.close();
});

test.afterAll(() => stopCopy());

for (const [name, settings] of Object.entries(STATES)) {
  test.describe(name, () => {
    let html = "";
    test.beforeAll(() => { if (settings.length) html = pageFrom(settings); });

    for (const [width, options] of [["1280", DESKTOP], ["390", PHONE]] as const) {
      for (const scheme of ["light", "dark"] as const) {
        test(`${width} ${scheme}`, async ({ browser }) => {
          mkdirSync("shots/money", { recursive: true });
          const ctx = await browser.newContext({ ...options, colorScheme: scheme, storageState: state });
          const page = await ctx.newPage();
          if (html) await page.route("**/admin/money", (route) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html }));
          await open(page, "/admin/money");
          await expect(page.locator("h1")).toHaveText("Financial Statistics");
          await expect(page.locator("[data-stat]")).toHaveCount(12);
          await expect(page.locator("#admin-money")).not.toContainText("None");
          await expect(page.locator("#money-ads")).toHaveAttribute("data-ad-state", name);
          expect(await page.content()).not.toContain(CLIENT);
          expect(await page.content()).not.toContain("1111111111");
          await expect(page.locator('a[href^="https://adsense.google.com/"]')).toBeVisible();

          const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
          expect(sideways, "sideways scroll").toBeLessThanOrEqual(0);
          const off = await page.evaluate(() => [...document.querySelectorAll("#admin-money *")].filter((el) => {
            const box = el.getBoundingClientRect();
            return box.width > 0 && box.right > document.documentElement.clientWidth + 0.5;
          }).length);
          expect(off, "something runs off the screen").toBe(0);

          // a figure's label keeps a readable width beside its number
          const labels = await page.evaluate(() => [...document.querySelectorAll("[data-stat] th")].map((th) => th.getBoundingClientRect().width));
          for (const label of labels) expect(label).toBeGreaterThan(200);

          const axe = await new AxeBuilder({ page }).include("#admin-money").analyze();
          expect(axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);

          await page.screenshot({ path: `shots/money/${name}-${width}-${scheme}.png`, fullPage: true });   // before anything scrolls: the bar is fixed
          await page.locator("#money-ads").screenshot({ path: `shots/money/${name}-${width}-${scheme}-ads.png` });
          await ctx.close();
        });
      }
    }
  });
}
