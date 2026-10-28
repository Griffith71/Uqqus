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
