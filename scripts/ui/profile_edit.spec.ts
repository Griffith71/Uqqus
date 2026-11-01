import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { open } from "./pages";

// The Edit profile pop-up (CLAUDE.md "Profile header"; templates/partials/profile_edit_modal.html, assets/js/profile_edit.js):
//   npm run profile-edit
// What it holds it to, in a real browser: the button and the "⋯" item open it without leaving the profile and give the focus back;
// Save is off until something differs; closing with changes asks first and Keep editing keeps them; one Save sends one request and the
// profile reads the change after it; a refusal is told beside its field and changes nothing; any account, with no Rep, has the camera buttons; with no script the button is still the link to Settings; on a phone it is a sheet from the bottom; axe finds nothing.
// The upload cases need `ui_profile_edit` (scripts/ui/seed_profile_edit.py, run in the container). The dev site has no storage bucket, so
// a picture cannot be stored there: those cases hold the FAILURE to what the plan promised (the old picture stays, the text still saves,
// the pop-up says which part failed). Every case that saves puts the profile back.

const VIEWER = "ui_viewer";
const PROFILE = `/@${VIEWER}`;
const UPLOADER = "ui_profile_edit";
const UPLOADER_PROFILE = `/@${UPLOADER}`;
const MODAL = "#profileEditModal";
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } as const;
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFElEQVR4nGNUXvmJARtgwio6aCUAXzwBzuwPunIAAAAASUVORK5CYII=", "base64");       // 8 x 8, a real picture

const BUTTON = ".profile-x-actions a.profile-x-pill[data-profile-edit]";       // not the ⋯ menu's item, which has the attribute too
const button = (page: Page) => page.locator(BUTTON);
const field = (page: Page, id: string) => page.locator(`${MODAL} #${id}`);

async function openModal(page: Page, path = PROFILE) {
  await open(page, path);
  await button(page).click();
  await expect(page.locator(`${MODAL}.show`)).toHaveCount(1);
  // Bootstrap ignores a hide() until its fade-in is over, and the first field takes the focus when it is (a fine pointer)
  await expect(field(page, "pe-display-name")).toBeFocused();
}

// what the account has now, read from the pop-up's own fields, so a case can put it back
async function snapshot(page: Page) {
  return page.evaluate(() => {
    const v = (id: string) => (document.getElementById(id) as HTMLInputElement).value;
    return { display_name: v("pe-display-name"), bio: v("pe-bio"), profile_job: v("pe-job"), profile_location: v("pe-location"), profile_website: v("pe-website") };
  });
}

async function restore(page: Page, values: Record<string, string>) {
  const formkey = await page.evaluate(() => (window as any).formkey());
  const answer = await page.request.post("/settings/profile/edit", { multipart: { formkey, ...values } });
  expect(answer.status(), await answer.text()).toBeLessThan(300);
}

async function signInAs(context: BrowserContext, name: string) {
  const page = await context.newPage();
  await page.goto("/login");
  await page.fill("#username", name);
  await page.fill("#password", "ui-Password-1");
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("#login_button")]);
  return page;
}

test.describe("signed in as the fixtures' viewer", () => {
  test.use({ storageState: ".auth/viewer.json", viewport: { width: 1280, height: 900 } });

  test("the button and the ⋯ item open it in place, and the focus goes back where it was", async ({ page }) => {
    await openModal(page);
    expect(new URL(page.url()).pathname).toBe(PROFILE);
    await page.keyboard.press("Escape");                           // nothing changed: it just closes
    await expect(page.locator(`${MODAL}.show`)).toHaveCount(0);
    await expect(button(page)).toBeFocused();

    await page.click("#profile-x-more");
    await page.locator(".profile-x-menu [data-profile-edit]").click();
    await expect(page.locator(`${MODAL}.show`)).toHaveCount(1);
    await expect(field(page, "pe-display-name")).toBeFocused();       // the fade-in is over (Bootstrap ignores a hide() until it is)
    expect(new URL(page.url()).pathname).toBe(PROFILE);
    await page.click("#profile-edit-close");
    await expect(page.locator(`${MODAL}.show`)).toHaveCount(0);
    await expect(page.locator("#profile-x-more")).toBeFocused();   // the menu closed with the click: its button gets the focus
  });

  test("it opens on what the profile has, and Save waits for a difference", async ({ page }) => {
    await openModal(page);
    const save = page.locator("#profile-edit-save");
    await expect(save).toBeDisabled();
    await expect(field(page, "pe-display-name")).toHaveAttribute("placeholder", VIEWER);
    await field(page, "pe-job").fill("A job");
    await expect(save).toBeEnabled();
    await field(page, "pe-job").fill("");                           // back to what it opened with
    await expect(save).toBeDisabled();
  });

  test("closing with changes asks first; Keep editing keeps them and Discard throws them away", async ({ page }) => {
    await openModal(page);
    const before = await snapshot(page);
    await field(page, "pe-bio").fill("Something I may not keep");
    await page.keyboard.press("Escape");
    await expect(page.locator("#profile-edit-discard")).toBeVisible();
    await expect(page.locator(`${MODAL}.show`)).toHaveCount(1);
    await page.getByRole("button", { name: "Keep editing" }).click();
    await expect(page.locator("#profile-edit-discard")).toBeHidden();
    await expect(field(page, "pe-bio")).toHaveValue("Something I may not keep");

    await page.click("#profile-edit-close");                        // the X asks the same way
    await expect(page.locator("#profile-edit-discard")).toBeVisible();
    await page.getByRole("button", { name: "Discard" }).click();
    await expect(page.locator(`${MODAL}.show`)).toHaveCount(0);
    await button(page).click();
    await expect(field(page, "pe-bio")).toHaveValue(before.bio);
  });

  test("one Save sends one request, and the profile reads every change after it", async ({ page }) => {
    await openModal(page);
    const before = await snapshot(page);
    const stamp = String(Date.now()).slice(-6);
    const requests: string[] = [];
    page.on("request", (r) => { if (r.url().includes("/settings/profile")) requests.push(`${r.method()} ${new URL(r.url()).pathname}`); });
    try {
      await field(page, "pe-display-name").fill(`UI Viewer ${stamp}`);
      await field(page, "pe-bio").fill(`Hello from the pop-up ${stamp}`);
      await field(page, "pe-job").fill("Tester");
      await field(page, "pe-location").fill("Lisbon");
      await field(page, "pe-website").fill("example.org");
      await Promise.all([page.waitForEvent("load"), page.click("#profile-edit-save")]);
      expect(requests.filter((r) => r.startsWith("POST"))).toEqual(["POST /settings/profile/edit"]);

      await expect(page.locator(".profile-x-name")).toContainText(`UI Viewer ${stamp}`);
      await expect(page.locator(".profile-x-bio")).toContainText(`Hello from the pop-up ${stamp}`);
      await expect(page.locator(".profile-x-details")).toContainText("Tester");
      await expect(page.locator(".profile-x-details")).toContainText("Lisbon");
      await expect(page.locator(".profile-x-details a[href='https://example.org']")).toHaveCount(1);
      await expect(page.locator("#toast-post-success")).toBeVisible();       // "Saved", carried over the reload
      await expect(page.locator(MODAL)).toBeHidden();
    } finally {
      await restore(page, before);
    }
    await open(page, PROFILE);
    await expect(page.locator(".profile-x-name")).toContainText(before.display_name || VIEWER);       // put back
  });

  test("a refusal is told beside its field, and changes nothing", async ({ page }) => {
    await openModal(page);
    const before = await snapshot(page);
    await field(page, "pe-display-name").fill("a@b");
    await field(page, "pe-job").fill("Would be saved");             // fine on its own: it must not be saved either
    await page.click("#profile-edit-save");
    const error = page.locator("#pe-error-display_name");
    await expect(error).toBeVisible();
    await expect(error).not.toHaveText("");
    await expect(field(page, "pe-display-name")).toHaveAttribute("aria-invalid", "true");
    await expect(field(page, "pe-display-name")).toBeFocused();
    await expect(page.locator(`${MODAL}.show`)).toHaveCount(1);
    await expect(page.locator("#profile-edit-save")).toBeEnabled();  // the member can fix it and try again
    await page.click("#profile-edit-close");
    await page.getByRole("button", { name: "Discard" }).click();
    await open(page, PROFILE);
    await button(page).click();
    expect(await snapshot(page)).toEqual(before);
  });

  test("an account with no Rep still has both camera buttons", async ({ page }) => {
    await openModal(page);
    await expect(page.locator(`${MODAL} [data-pe-pick]`)).toHaveCount(2);
    await expect(page.locator(MODAL)).not.toContainText("Rep");
  });

  for (const theme of ["light", "dark"] as const) {
    test(`axe finds nothing serious in the open pop-up (${theme})`, async ({ browser }) => {
      const context = await browser.newContext({ storageState: ".auth/viewer.json", colorScheme: theme, viewport: { width: 1280, height: 900 } });
      const page = await context.newPage();
      await openModal(page);
      const { violations } = await new AxeBuilder({ page }).include(MODAL).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) }));
      expect(serious).toEqual([]);
      await context.close();
    });
  }
});

test("a phone gets a sheet from the bottom, with targets a finger can hit", async ({ browser }) => {
  const context = await browser.newContext({ storageState: ".auth/viewer.json", ...PHONE });
  const page = await context.newPage();
  await open(page, PROFILE);
  await page.locator(BUTTON).click();
  await expect(page.locator(`${MODAL}.show`)).toHaveCount(1);
  await page.waitForTimeout(400);                                   // the slide in
  const box = await page.evaluate(() => {
    const r = document.querySelector("#profileEditModal .modal-content")!.getBoundingClientRect();
    return { left: r.left, right: r.right, bottom: r.bottom, innerWidth, innerHeight };
  });
  expect(box.left).toBeLessThanOrEqual(8);                          // Bootstrap's 7px dialog margin, as every phone sheet has
  expect(box.right).toBeGreaterThanOrEqual(box.innerWidth - 8);
  expect(box.bottom).toBeGreaterThanOrEqual(box.innerHeight - 8);   // it sits on the bottom edge (less the dialog's margin)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  for (const sel of ["#profile-edit-close", "#profile-edit-save"]) {
    const h = await page.locator(sel).evaluate((e) => e.getBoundingClientRect().height);
    expect(h, sel).toBeGreaterThanOrEqual(43.9);
  }
  await context.close();
});

test("with no script the button is still the link to Settings", async ({ browser }) => {
  const context = await browser.newContext({ storageState: ".auth/viewer.json", javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto(PROFILE, { waitUntil: "load" });
  await page.locator(BUTTON).click();
  await page.waitForURL("**/settings/profile");
  await context.close();
});

test.describe("an account that may upload", () => {
  // the dev site has no storage bucket: a picture can't be stored, so these hold the failure to what was promised
  test("a file that is not a picture is turned away before anything is sent; a picture can be taken back", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await signInAs(context, UPLOADER);
    await openModal(page, UPLOADER_PROFILE);
    await expect(page.locator(`${MODAL} [data-pe-pick="profile"]`)).toHaveCount(1);
    await expect(page.locator(`${MODAL} [data-pe-pick="banner"]`)).toHaveCount(1);

    let posted = 0;
    page.on("request", (r) => { if (r.url().endsWith("/settings/profile/edit")) posted++; });
    await page.setInputFiles("#pe-file-profile", { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("not a picture") });
    await expect(page.locator("#pe-error-profile")).toBeVisible();
    await expect(page.locator("#profile-edit-save")).toBeDisabled();
    expect(posted).toBe(0);

    const stored = await page.locator("#pe-avatar-img").getAttribute("src");
    await page.setInputFiles("#pe-file-profile", { name: "me.png", mimeType: "image/png", buffer: PNG });
    await expect(page.locator("#pe-avatar-img")).toHaveAttribute("src", /^blob:/);
    await expect(page.locator("#profile-edit-save")).toBeEnabled();
    await page.locator('[data-pe-remove="profile"]').click();       // a picture chosen but not sent: this takes it back
    await expect(page.locator("#pe-avatar-img")).toHaveAttribute("src", stored!);
    await expect(page.locator("#profile-edit-save")).toBeDisabled();
    await context.close();
  });

  test("a picture the site can't store leaves the old one, still saves the text, and says which part failed", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await signInAs(context, UPLOADER);
    await openModal(page, UPLOADER_PROFILE);
    const before = await snapshot(page);
    const stamp = String(Date.now()).slice(-6);
    const avatarBefore = await page.locator(".profile-x-avatar-img").getAttribute("src");
    try {
      await field(page, "pe-job").fill(`Job ${stamp}`);
      await page.setInputFiles("#pe-file-profile", { name: "me.png", mimeType: "image/png", buffer: PNG });
      await page.click("#profile-edit-save");
      await expect(page.locator("#pe-error-profile")).toBeVisible();              // told beside the picture
      await expect(page.locator("#pe-error-profile")).not.toHaveText("");
      await expect(page.locator(`${MODAL}.show`)).toHaveCount(1);
      await expect(page.locator("#profile-edit-save")).toBeEnabled();             // the picture is still chosen: try again
      await page.click("#profile-edit-close");                                    // the text went through, so closing reloads the page behind
      await Promise.all([page.waitForEvent("load"), page.getByRole("button", { name: "Discard" }).click()]);
      await expect(page.locator(".profile-x-details")).toContainText(`Job ${stamp}`);
      await expect(page.locator(".profile-x-avatar-img")).toHaveAttribute("src", avatarBefore!);
    } finally {
      await restore(page, before);
    }
    await context.close();
  });
});
