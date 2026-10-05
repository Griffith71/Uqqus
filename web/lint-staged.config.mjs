// Staged-file checks only; the full suite runs in CI (`npm run check`).
const config = {
  "*.{ts,tsx,js,jsx,mjs,mts}": ["eslint --fix", "prettier --write"],
  "*.{json,css,md,yml,yaml}": "prettier --write",
  // tsc can't check single files, so run the project typecheck when TS is staged.
  "*.{ts,tsx}": () => "npm run typecheck",
};

export default config;
