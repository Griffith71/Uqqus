#!/usr/bin/env sh
# Install the git hooks (.husky/pre-commit, .husky/post-commit) on a fresh clone. They used to be installed by
# `npm install` in web/ (its `prepare` script); web/ is parked, so this does it directly.
#   sh scripts/install_hooks.sh
set -e
cd "$(dirname "$0")/.."
chmod +x .husky/pre-commit .husky/post-commit 2>/dev/null || true
git config core.hooksPath .husky
echo "git hooks installed (core.hooksPath = .husky)"
