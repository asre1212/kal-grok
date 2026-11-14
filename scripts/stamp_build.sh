#!/usr/bin/env bash
# Stamp the PWA build id into docs/ before deploying (CI only; the repo keeps __BUILD__).
# The id is a hash of the app-shell files, so it changes only when the app's code changes
# (data-only bot commits don't force the installed app to reload), plus the short git SHA
# of the commit that last changed the shell for traceability.
set -euo pipefail
cd "$(dirname "$0")/.."
SHELL_FILES="docs/index.html docs/app.js docs/style.css docs/sw.js docs/manifest.json"
HASH=$(cat $SHELL_FILES | sha256sum | cut -c1-8)
SHA=$(git log -1 --format=%h -- $SHELL_FILES 2>/dev/null || true)
SHA=${SHA:-local}
BUILD="${SHA}-${HASH}"
sed -i "s/__BUILD__/${BUILD}/g" docs/index.html docs/app.js docs/sw.js
echo "$BUILD"
