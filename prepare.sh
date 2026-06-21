#!/usr/bin/env bash
# Maintainer script — regenerates the static assets the package ships with, so the
# published package needs no network at import:
#   - src/pexafy_mcp/assets/openapi.json   (snapshot of {API}/schema.json)
#   - src/pexafy_mcp/assets/facets.json    (evolving source/license value sets)
#   - src/pexafy_mcp/assets/ext_apps_bundle.js  (vendored ext-apps SDK)
# Needs PEXAFY_API_BASE_URL (+ a key for facets) in .env. Run it, review the diff,
# commit the assets. Not bundled into the runtime image (see .dockerignore).
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a || true

A="src/pexafy_mcp/assets"
API="${PEXAFY_API_BASE_URL:?set PEXAFY_API_BASE_URL in .env}"
KEY="${PEXAFY_FACET_BOOTSTRAP_KEY:-${PEXAFY_API_KEY:-}}"
EXT_APPS_VERSION="${EXT_APPS_VERSION:-1.7.4}"

echo "1/3  OpenAPI snapshot  <- ${API%/}/schema.json"
curl -fsSL "${API%/}/schema.json" | python3 -m json.tool > "$A/openapi.json"

echo "2/3  facet value sets (sources, licenses)"
[ -n "$KEY" ] || { echo "  ERROR: need a key (PEXAFY_FACET_BOOTSTRAP_KEY) in .env for facets"; exit 1; }
python3 - "$API" "$KEY" > "$A/facets.json" <<'PY'
import sys, json, urllib.request
api, key = sys.argv[1].rstrip("/"), sys.argv[2]
def fetch(ep):
    req = urllib.request.Request(f"{api}/api/v1/facets/{ep}", headers={"x-api-key": key})
    data = json.load(urllib.request.urlopen(req, timeout=15))
    return [r["name"] for r in (data.get("data") or []) if r.get("name")]
json.dump({"source": fetch("sources"), "license_type": fetch("licenses")}, sys.stdout, indent=2)
print()
PY

echo "3/3  ext-apps SDK bundle @ ${EXT_APPS_VERSION}"
curl -fsSL "https://cdn.jsdelivr.net/npm/@modelcontextprotocol/ext-apps@${EXT_APPS_VERSION}/dist/src/app-with-deps.js" -o "$A/ext_apps_bundle.js"

echo "Done. Review 'git diff $A' and commit the regenerated assets."
