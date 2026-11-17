#!/usr/bin/env bash
set -euo pipefail

# Lock the installation across containers. npm ci replaces node_modules, so
# the lock itself lives on the shared npm cache volume.
(
    flock 9
    fingerprint=$( { sha256sum package.json package-lock.json; node --version; npm --version; uname -m; } | sha256sum )
    stamp=node_modules/.aid-dev-fingerprint
    if [[ ! -f "$stamp" || $(<"$stamp") != "$fingerprint" ]]; then
        npm ci --no-audit
        printf '%s\n' "$fingerprint" > "$stamp"
    fi
) 9>/root/.npm/aid-dev-web-install.lock

exec npm "$@"
