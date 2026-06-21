# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
