"""Shared test setup.

Every test here is OFFLINE: the server is built from the vendored OpenAPI snapshot
and no module performs network I/O at import or build time. We pin a few env vars to
deterministic values BEFORE importing the package so the import-time configuration in
each module is predictable regardless of the developer's local .env.
"""
from __future__ import annotations

import os

# Pin import-time config before pexafy_mcp modules are imported by the tests.
os.environ.setdefault("PEXAFY_API_BASE_URL", "http://localhost:8000")
os.environ.setdefault("PEXAFY_MCP_TRANSPORT", "stdio")
# Keep previews deterministic: tests that need them on monkeypatch the module directly.
os.environ.setdefault("PEXAFY_THUMB_BASE_URL", "")
os.environ.setdefault("PEXAFY_THUMB_HMAC_SECRET", "")
