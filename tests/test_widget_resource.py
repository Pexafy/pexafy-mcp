"""The metadata ChatGPT reads on the inline-grid MCP App resource.

Two facts travel twice: FastMCP writes the spec form under `_meta.ui`, and
ChatGPT reads its own compatibility aliases, `openai/widgetDomain` and
`openai/widgetCSP`. Both are written from the same values in build_server, and
these tests exist to keep them from drifting apart — a drift nothing else would
catch, because each side is invisible to the other's reader.

The domain matters at submission: without it OpenAI's portal refuses the app
("a unique domain is required for app validation"). The CSP alias matters after
it: it decides whether thumbnails load once the app is published rather than
previewed, which cannot be tested before publishing.
"""
from __future__ import annotations

import importlib

import pytest

from pexafy_mcp import previews, server


@pytest.fixture
def previews_on(monkeypatch):
    """The grid is only registered when the thumbnail CDN is configured."""
    monkeypatch.setenv("PEXAFY_THUMB_BASE_URL", "https://thumb.pexafy.com")
    monkeypatch.setenv("PEXAFY_THUMB_HMAC_SECRET", "test-secret")
    importlib.reload(previews)
    yield
    importlib.reload(previews)


async def _grid_meta():
    for resource in await server.build_server().list_resources():
        if str(resource.uri) == server.widget.GRID_URI:
            return resource.to_mcp_resource().meta or {}
    raise AssertionError("the grid resource is not registered")


async def test_the_widget_domain_is_declared_both_ways(previews_on):
    meta = await _grid_meta()
    assert meta["openai/widgetDomain"] == server.WIDGET_DOMAIN
    assert meta["ui"]["domain"] == server.WIDGET_DOMAIN


async def test_the_widget_domain_is_an_https_origin_with_no_path(previews_on):
    """It is an origin, not a URL: a trailing path fails validation."""
    domain = (await _grid_meta())["openai/widgetDomain"]
    assert domain.startswith("https://")
    assert domain.count("/") == 2, domain


async def test_the_two_csp_encodings_carry_the_same_domains(previews_on):
    """The spec form is camelCase, the alias snake_case; the values must match."""
    meta = await _grid_meta()
    spec, alias = meta["ui"]["csp"], meta["openai/widgetCSP"]
    assert alias["resource_domains"] == spec.get("resourceDomains", [])
    assert alias["connect_domains"] == spec.get("connectDomains", [])


async def test_the_thumbnail_origin_is_allowed_to_load(previews_on):
    """Whatever else changes, the grid must be able to fetch its thumbnails."""
    meta = await _grid_meta()
    assert previews.THUMB_ORIGIN in meta["openai/widgetCSP"]["resource_domains"]


def test_the_default_domain_is_not_openais_shared_sandbox():
    """The shared default is precisely what the portal refuses."""
    assert "oaiusercontent.com" not in server.WIDGET_DOMAIN
    assert server.WIDGET_DOMAIN.startswith("https://")
