# UI checks (legacy app)

Screenshots and an accessibility pass of the running site, for checking UI work before and after a change.
Dev-only: nothing here is loaded by the site.

1. Run the site (`docker compose up`) against a **scratch** database, never real data, and fill it:
   `PYTHONPATH=. python scripts/seed_ui_fixtures.py` (inside the container; it refuses any database not named
   `ui_preview`, `ci_*` or `test*`). It writes `.fixtures.json` here with the ids of the posts below.
2. `npm install` here (the browsers come from Playwright's cache: `npx playwright install chromium` if missing).
3. `SHOTS_LABEL=before npm run shots`, make the change, `SHOTS_LABEL=after npm run shots`, and compare
   `shots/before/<page>/<width>-<theme>.png` with `shots/after/...` (390, 768, 1280 and 1440px, light and dark).
4. `SHOTS_LABEL=after npm run axe` writes the serious and critical axe findings to `reports/axe-after.json`.

`SHOTS_ONLY=all,post-thread` and `SHOTS_WIDTHS=390,1280` narrow a run while iterating. `UI_BASE_URL` points it somewhere other than http://localhost. The pages are listed in `pages.ts`.

**Languages** (`i18n.spec.ts`): `I18N_LABEL=before npm run i18n` switches every ready language (a visitor with the footer
switcher, the signed-in viewer through Settings > Content), checks `<html lang dir>`, and measures every piece of text
on the pages above at 390 and 1280px against the English render: cut off, wrapping where English took one line,
sticking out of its box, much wider than in English, and the page scrolling sideways. It writes
`reports/i18n-<label>/<lang>.json`; `PYTHONPATH=. ../../.venv/bin/python i18n_summary.py <label>` (from the repo root:
`scripts/ui/i18n_summary.py`) lists the labels behind them and their catalogue entries. `I18N_LANGS=fr,de`,
`I18N_PAGES=all,profile` and `I18N_WIDTHS=390` narrow a run. It sets the viewer back to English at the end.

**The light language** (`light.spec.ts`, `npm run light`): measures, in real pixels, that the light never lowers text's contrast (a message you sent must stay AA at the top, middle and bottom of a scrolled thread, and is darker toward the bottom), that a primary button is a flat fill whose hover and press only darken it, that a new message glints once and nothing runs on a clock on load, and that `prefers-reduced-motion`, forced colours and `prefers-contrast: more` switch the effects off. axe cannot see an overlay, so this is the check for it.

**Glass** (`glass.spec.ts`, `npm run glass`): measures in a real browser that the blur is real (stripes behind a dialog's card come out flat, and show when the blur is taken off), that no ancestor of a glass card is a backdrop root at any frame of an open, that a feed card's menu is glass and on top of the next card, that every glass surface is solid and unblurred under reduced transparency (set through the DevTools protocol: Playwright has no option), more contrast and forced colours, that the sensitive-content cover works from the keyboard, and that Block asks in the frosted sheet, makes no request until told to, can be undone and leaves no dialog, backdrop or scroll lock behind. It unblocks the fixture account it uses even if it fails.

**Sizing and spacing** (`sizing.spec.ts`, `npm run sizing`): measures what CSS can only claim, because 36px in a rule can still render 35.6 or 38. The bar is 56px (52 in Compact) with 36px controls, 14px glyphs on a 36px pitch and a 32px avatar; the account menu is 264px wide with 36px rows, a 56px profile row and 8px of inset, scrolls itself in a short window, and grows with the largest Text size without cutting anything off; on a touch screen it is a top sheet attached to the bar with 44px rows and the page dimmed under it. Also under a finger: the phone's sort bar stays clear of the feed's tabs, a visitor's bar is the same 56px, round icon buttons stay round, the editor's toolbar stays one row and an anchor styled as a button centres its label. Nothing scrolls sideways at 390px and the small text links have a 24px target.

**Shells** (`shells.spec.ts`, `npm run shells`): one page per page shell (the feeds, settings and Create post and a guild's mod page, help and legal, About and Donate), at 1280 and 390px, must show the same 56px fixed glass bar with the mark, Noto Sans, a flat button and no sideways scroll; the member shells' icon rows are the same 36px targets on a 36px pitch starting at the same place; the help bar's phone menu opens into 36px rows and closes; and the docs photo leans blue, not the old purple. Add a page here when a new shell is made.
