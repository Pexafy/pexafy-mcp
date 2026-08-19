"""A directory that cannot get past the auth wall still has to be able to read us.

`initialize` and `tools/list` require a token — Smithery's own guidance is that an
OAuth server SHOULD answer 401 so the flow is discovered — so a scanner is left
with nothing to put on a page. Our Smithery listing was the proof: created, empty,
its scan recorded as AUTH_TIMEOUT, no tools, no description. Their bot had asked
for `/.well-known/mcp/server-card.json` seconds before giving up, and got a 404.

The card answers that, unauthenticated, and is generated from the live server so it
cannot describe tools the server does not have.
"""
import json
from pathlib import Path

import pytest

PATH = "/.well-known/mcp/server-card.json"
OAUTH_ENV = {
    "PEXAFY_MCP_TRANSPORT": "http",
    "PEXAFY_OAUTH_RESOLVE_URL": "http://django/oauth/mcp/resolve",
    "MCP_RESOLVE_SECRET": "test-secret",
    "PEXAFY_OAUTH_AS_URL": "https://pexafy.com",
    "PEXAFY_MCP_PUBLIC_URL": "https://mcp.pexafy.com",
}


def _reload(monkeypatch, env):
    import importlib
    import os

    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from pexafy_mcp import server

    return importlib.reload(server)


def _get(app, path):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        return client.get(path)


@pytest.fixture
def card(monkeypatch):
    server = _reload(monkeypatch, OAUTH_ENV)
    response = _get(server.build_server().http_app(), PATH)
    assert response.status_code == 200
    return response.json()


def test_the_card_is_readable_without_credentials(card):
    """The whole point: the endpoint that needs a token is not the one describing it."""
    assert card["serverInfo"]["name"]
    assert card["serverInfo"]["version"]


def test_it_declares_the_auth_wall_it_sits_behind(card):
    assert card["authentication"] == {"required": True, "schemes": ["oauth2"]}


def test_it_lists_the_tools_the_server_actually_serves(card):
    names = {tool["name"] for tool in card["tools"]}
    assert names == {"search_photos", "search_photos_by_image", "get_similar_photos"}


def test_every_listed_tool_carries_a_callable_schema(card):
    """A name and a sentence is not enough for a client to decide it can call us."""
    for tool in card["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_the_card_says_no_auth_when_the_server_has_none(monkeypatch):
    """Local/stdio use: claiming OAuth there would send a scanner looking for one."""
    server = _reload(monkeypatch, {"PEXAFY_MCP_TRANSPORT": "http"})
    card = _get(server.build_server().http_app(), PATH).json()
    assert card["authentication"] == {"required": False, "schemes": []}


def test_the_display_title_matches_the_registry_manifest():
    """Two listings of the same server should not carry two different names."""
    from pexafy_mcp import server

    manifest = json.loads((Path(__file__).resolve().parents[1] / "server.json").read_text())
    assert server.SERVER_TITLE == manifest["title"]
