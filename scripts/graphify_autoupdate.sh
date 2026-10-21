#!/usr/bin/env bash
# Refresh graphify-out once enough code files changed since the last build.
# Run by the Claude Code Stop hook (.claude/settings.json). Silent, and never fails the turn.
# Threshold: GRAPHIFY_AUTO_THRESHOLD (default 15). `graphify update` is AST-only: no LLM, no cost.
cd "$(dirname "$0")/.." || exit 0

GRAPH=graphify-out/graph.json
LOCK=graphify-out/.autoupdate.lock
THRESHOLD="${GRAPHIFY_AUTO_THRESHOLD:-15}"

[ -f "$GRAPH" ] || exit 0
command -v graphify >/dev/null 2>&1 || exit 0

# A lock younger than 10 minutes means a rebuild is already running.
if [ -f "$LOCK" ] && [ -z "$(find "$LOCK" -mmin +10 2>/dev/null)" ]; then
  exit 0
fi

CHANGED=$(find ruqqus tests scripts web/src -type f \
  \( -name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.tsx' \) \
  -not -path '*/node_modules/*' -not -name '*_bundle.js' \
  -newer "$GRAPH" 2>/dev/null | wc -l)

[ "$CHANGED" -ge "$THRESHOLD" ] || exit 0

touch "$LOCK"
(graphify update . >/dev/null 2>&1; rm -f "$LOCK") &
exit 0
