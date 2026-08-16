# Third-party notices

`pexafy-mcp` itself is MIT-licensed (see [LICENSE](LICENSE)). That licence covers the
code in this repository only.

The package **also redistributes** the third-party files below, vendored under
`src/pexafy_mcp/assets/` so the server needs no network at import or in the browser
sandbox. Each keeps its own licence; full texts are in [`licenses/`](licenses/).

---

## 1. Inter (typeface)

- **Files:** `src/pexafy_mcp/assets/inter-latin.woff2`, `src/pexafy_mcp/assets/inter-latin-ext.woff2`
- **Upstream:** https://github.com/rsms/inter
- **Licence:** SIL Open Font License 1.1 — [`licenses/OFL-1.1-Inter.txt`](licenses/OFL-1.1-Inter.txt)
- **Copyright:** Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter)

Subset to the Latin and Latin-Extended ranges and inlined (base64) into the result-grid
widget. The font files themselves are unmodified in outline or metadata.

---

## 2. @modelcontextprotocol/ext-apps

- **File:** `src/pexafy_mcp/assets/ext_apps_bundle.js` (version 1.7.4, browser bundle)
- **Upstream:** https://github.com/modelcontextprotocol/ext-apps
- **Licence:** Apache-2.0 — [`licenses/Apache-2.0-ext-apps.txt`](licenses/Apache-2.0-ext-apps.txt)
- **Copyright:** Copyright (c) Anthropic, PBC and the Model Context Protocol contributors

Note on licence status: the npm package metadata for 1.7.4 declares `MIT`, while the
upstream repository's `LICENSE` states the MCP project is transitioning from MIT to
Apache-2.0 — new contributions are Apache-2.0, contributions whose authors have not
consented to relicensing remain MIT. The bundle may therefore contain code under either
licence, so the full Apache-2.0 text (including the transition statement) is reproduced.

**Modification notice (Apache-2.0 §4(b)):** the vendored file is byte-for-byte upstream,
but this project modifies it *at runtime* before serving it. `widget._rebind_exports_to_global`
rewrites the bundle's trailing `export { … }` map into an assignment on `globalThis`, so a
second inline `<script type="module">` can consume the SDK without any network fetch — the
claude.ai app sandbox blocks external script imports. No other change is made.

---

## 3. Zod

- **Bundled inside:** `src/pexafy_mcp/assets/ext_apps_bundle.js`
- **Upstream:** https://github.com/colinhacks/zod
- **Licence:** MIT — [`licenses/MIT-zod.txt`](licenses/MIT-zod.txt)
- **Copyright:** Copyright (c) 2025 Colin McDonnell

Zod is bundled into the ext-apps browser build rather than being a separate file, so its
notice is reproduced here.

---

## 4. @standard-schema/spec

- **Bundled inside:** `src/pexafy_mcp/assets/ext_apps_bundle.js`
- **Upstream:** https://github.com/standard-schema/standard-schema
- **Licence:** MIT — [`licenses/MIT-standard-schema.txt`](licenses/MIT-standard-schema.txt)
- **Copyright:** Copyright (c) 2024 Colin McDonnell

---

## Regenerating the vendored assets

`./prepare.sh` refreshes `assets/openapi.json`, `assets/facets.json` and
`assets/ext_apps_bundle.js`. If it pulls a **new version** of the ext-apps bundle, re-check
this file: the version number above, and whether the bundle's own dependency set changed.
