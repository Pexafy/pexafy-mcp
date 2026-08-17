# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.3] — 2026-08-17

### Changed
- Tool descriptions now describe the result instead of instructing the assistant.
  The appended block used to say: always prefix results with their rank,
  proactively offer more like one of them, never blame the browser when the
  images do not show. Anthropic's connector review rejects exactly that —
  "Describe what the tool does. Do not tell Claude how to behave" — and names
  telling an assistant to call a tool the user did not ask for as a
  prompt-injection pattern. The facts stay: what `rank`, `photo_id`,
  `attribution` and `urls` contain, and that some clients render the inline grid
  only inside an expandable panel. An assistant that knows the rank is the handle
  a person uses, and that the matching `photo_id` sits beside it, connects the two
  without being ordered to.

## [0.3.2] — 2026-08-17

### Added
- Every tool now carries MCP annotations: `readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint` and a display title. A host uses these to
  decide whether a call needs the user's confirmation, and Anthropic's connector
  directory rejects a submission whose tools declare none. All three tools search
  and return; not one writes, and the annotations now say so.

## [0.3.1] — 2026-08-17

### Fixed
- The protected-resource metadata (RFC 9728) is now served at the well-known
  **root** as well as under the resource path. Clients that do not implement the
  path insertion probed `/.well-known/oauth-protected-resource`, got a 404, and
  concluded the server had no authentication — a connector directory listed it as
  "No Auth" and failed its connection test on that basis, against a server that
  had answered 401 with a `WWW-Authenticate` header naming the real document.
  One handler, two mounts, so the two cannot drift apart.

## [0.3.0] — 2026-08-17

Packaging and distribution. The server's behaviour is unchanged; the container's
default is not.

### Changed
- **The image now defaults to the `stdio` transport, not `http`.** This is how an
  MCP client drives a containerised server, and `docker run -i --rm pexafy-mcp`
  previously hung — listening on a port nobody was talking to. Both compose files
  set the transport explicitly, so serving over HTTP is unaffected; anyone who
  relied on the old default must now pass `PEXAFY_MCP_TRANSPORT=http`.
- The README leads with the hosted server rather than with self-hosting, and
  documents the three tools with their full parameter signatures.
- `docker-compose.pexafy.yml`, which described Pexafy's own deployment, is
  replaced by a neutral `docker-compose.example.yml`.
- CI moved from GitLab to GitHub Actions, with ruff pinned so a new release of
  the linter cannot fail an untouched tree.

### Added
- `THIRD_PARTY_NOTICES.md` and `licenses/`: the package redistributes the Inter
  typeface (SIL OFL 1.1) and the `@modelcontextprotocol/ext-apps` bundle
  (Apache-2.0, with zod and `@standard-schema/spec` inside it). None of them
  shipped with its notice.
- `server.json`, the manifest the official MCP registry consumes. Published as
  `com.pexafy/pexafy-mcp`, namespace validated by DNS.
- `glama.json`, declaring the maintainer so the Glama listing can be claimed.
- Screenshots of the inline result grid, the rank follow-up and the detail panel.

## [0.2.0] — 2026-06-21

First public release.

### Added
- MCP server exposing Pexafy image search to any MCP client, with three tools:
  `search_photos` (semantic text), `search_photos_by_image` (visual search from an
  image URL), and `photo_similar` ("more like this").
- Inline thumbnail result grid as an MCP Apps UI resource (self-contained widget,
  vendored ext-apps SDK, signed thumbnail URLs).
- `stdio` and remote Streamable `http` transports.
- Per-user auth over HTTP: OAuth Resource Server or a forwarded Pexafy API key.
- In-chat, metric-driven plan-limit messages (rate limit vs monthly quota).
- `build_server()` factory with no import-time side effects or network — tools are
  generated from a vendored OpenAPI snapshot.
- Offline `pytest` test suite (server build, tool tuning, preview signing, limit
  copy) wired into CI alongside `ruff`.

### Changed
- The Compose file is now `docker-compose.pexafy.yml` and documented as Pexafy's own
  production deployment (joins the internal network), not a generic `docker compose up`.
