# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.7] — 2026-08-20

### Fixed
- **Every parameter of `search_photos_by_image` now says what it is.** The two
  generated tools inherit their parameter descriptions from the spec; the
  hand-written by-image tool inherited what a Python signature carries — nothing —
  and shipped twelve bare parameters, leaving a model to guess `text_alpha` from
  its name while the identical parameter is documented on `search_photos`. The
  filters now take their wording from the operation this tool posts to (excluded
  from tool generation, but still the authority on those parameters), so the two
  cannot drift; only the image inputs are written by hand. Two tests: one fails on
  any undescribed parameter of any tool, the other on any divergence from the spec.
  It was also the single point missing from the Smithery quality score
  (Parameter descriptions 2/3, 97/100).

## [0.4.6] — 2026-08-20

### Removed
- **`/.well-known/glama.json` — it fixed nothing, so it goes.** 0.4.5 served it
  because Glama's crawler asks for it and the connector listing complained it was
  missing. Serving it turned that complaint into a different one: their connector
  validator rejects the shape their own published schema defines
  (`maintainers[0]`: *"expected object, received string"*), and no MCP server in
  the wild publishes any other shape — every hosted server checked answers 404
  there, which is what the listing was reporting in the first place. The
  maintainer claim was never read from that URL anyway: it comes from the
  repository file, and the server page still shows the verified-maintainer badge.
  Back to 404, like everyone else. The server card stays — that one demonstrably
  works: it took the Smithery release from AUTH_TIMEOUT to SUCCESS.

## [0.4.5] — 2026-08-20

### Added
- **Pre-connect server card at `/.well-known/mcp/server-card.json`.** A directory
  that cannot authenticate has nothing to show: our Smithery listing was created,
  then left empty with its scan recorded as `AUTH_TIMEOUT`, no tools and no
  description — and their bot had asked for this exact path seconds before giving
  up. Smithery documents the card as the escape hatch for precisely that case
  ("automatic scanning can't complete (auth wall, ...)"), and it is the shape the
  pre-connect discovery draft (SEP-2127) and a growing crowd of scanners probe.
  The endpoint keeps its 401 — that is what makes clients discover OAuth — while
  the card, generated from the live server, states the identity, the auth wall and
  the three tools with their real schemas. Six tests, including one that fails if
  the card ever claims a tool the server does not serve.

### Fixed
- **The maintainer claim is now served where Glama actually reads it.** Its
  connector listing said `glama.json not found (HTTP 404)` while the file sat on
  the default branch of the public repository — because it does not read the
  repository for a hosted connector: it fetches `/.well-known/glama.json` on the
  host serving the MCP server, unauthenticated, every half hour (231 such 404s in
  one prod access log). The same claim is now answered there. It is held in code,
  not read from the repository file, because the image ships `src/` only; a test
  pins the two copies together so they cannot drift.

## [0.4.4] — 2026-08-20

### Added
- **`search_photos_by_image` now describes its results.** The two tools generated
  from the OpenAPI spec inherit an output schema; the hand-written by-image tool
  declared none, and ChatGPT surfaces that gap to the user as a badge on the tool
  itself — not only in the submission form. It now borrows `search_photos`' schema
  rather than restating it: both post to the same endpoint and return the same
  envelope, so a copy would be a second source of truth free to drift from the
  spec every other tool follows. Two tests hold it, one of which fails the moment
  any tool ships without an output schema.

## [0.4.3] — 2026-08-20

### Fixed
- **The version was written in three files and one was left behind.** 0.4.2
  shipped with `src/pexafy_mcp/__init__.py` still reading 0.4.1, which is the
  string the deploy script prints as "version on disk" — so the one place a human
  looks to confirm what production is running was the one place that was wrong.
  Three tests now hold `pyproject.toml`, `__init__.py` and `server.json` together,
  and require the version to have a changelog entry.

## [0.4.2] — 2026-08-20

### Fixed
- **OpenAI's app-directory scan rejected the server** on the schema of
  `search_photos_by_image`'s `image_file` parameter: *"file parameter 'image_file'
  must use the documented file schema"*. The parameter is typed `dict | None`, from
  which Pydantic infers `{"anyOf": [{"type": "object"}, {"type": "null"}]}` —
  declaring no properties at all, where the Apps SDK fixes the shape a host fills
  in: all four of `download_url`, `file_id`, `mime_type` and `file_name` declared,
  the first two required, the other two not, and no additional field. The schema
  is now written out and stamped onto the tool, and the parameter stays optional by
  being absent from `required` rather than by being nullable, since the `anyOf`
  wrapper is exactly what hid the properties. Three tests hold the contract.

## [0.4.1] — 2026-08-19

### Fixed
- **The key-limit message asked for a reconnection that is never needed.** It
  ended with "then reconnect", which is not true: the server resolves the OAuth
  token against Django on every request, with no cache, so the moment a key slot
  frees up the next question simply works. It also called the keys "connectors",
  a word that appears nowhere on the page it links to — users arrived looking for
  a list of connectors and found a list of API keys.

## [0.4.0] — 2026-08-18

### Changed
- **Plan-limit messages no longer sell.** They used to name the next tier, its
  price and link to the pricing page. OpenAI's plugin policy forbids that —
  "Plugins must not display subscription plans, initiate new subscriptions, or
  promote upgrades", with freemium upsells named explicitly — while allowing what
  actually helps: "the plugin may explain that [a feature requires a different
  plan]". So each message now states the limit, the number and the plan it belongs
  to, and stops. The rate-limit message keeps its wait instruction, without which
  an assistant retries in a loop and burns the same budget.

### Removed
- The plan-ladder machinery behind that copy: the cached fetch of
  `/api/v1/billing/plans`, the next-tier lookup, the price and label helpers.
  Nothing reads them now, and the error path no longer makes an HTTP call of its
  own. The module went from 144 lines to 83.

## [0.3.5] — 2026-08-17

### Added
- `/.well-known/openai-apps-challenge`, the domain-ownership check for OpenAI's app
  directory. Their verification fetches it on the host serving this server and
  expects the bare token — no JSON, no wrapper. Served only when
  `OPENAI_APPS_CHALLENGE` is set; the path 404s otherwise, rather than answering
  with an empty body that an ownership check could read as a pass.

## [0.3.4] — 2026-08-17

### Changed
- `photo_similar` is now **`get_similar_photos`**. A tool name should read as the
  action it performs; both connector directories say so, and OpenAI's own example
  is `get_order_status`. The name is overridden where the tools are generated
  rather than in the API's operationId, so regenerating the vendored spec cannot
  silently undo it.

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
