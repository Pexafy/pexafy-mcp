#!/usr/bin/env bash
# Maintainer script — regenerates the static assets the package ships with, so the
# published package needs no network at import:
#   - src/pexafy_mcp/assets/openapi.json   (snapshot of {API}/schema.json)
#   - src/pexafy_mcp/assets/ext_apps_bundle.js  (vendored ext-apps SDK)
# Needs PEXAFY_API_BASE_URL in .env. Run it, review the diff, commit the assets.
# Not bundled into the runtime image (see .dockerignore).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a || true

A="src/pexafy_mcp/assets"
API="${PEXAFY_API_BASE_URL:?set PEXAFY_API_BASE_URL in .env}"
EXT_APPS_VERSION="${EXT_APPS_VERSION:-1.7.4}"

echo "1/2  OpenAPI snapshot  <- ${API%/}/schema.json"
curl -fsSL "${API%/}/schema.json" | python3 -m json.tool > "$A/openapi.json"

echo "2/2  ext-apps SDK bundle @ ${EXT_APPS_VERSION}"
curl -fsSL "https://cdn.jsdelivr.net/npm/@modelcontextprotocol/ext-apps@${EXT_APPS_VERSION}/dist/src/app-with-deps.js" -o "$A/ext_apps_bundle.js"

echo "Done. Review 'git diff $A' and commit the regenerated assets."
