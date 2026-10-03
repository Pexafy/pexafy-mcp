"""A directory that cannot get past the auth wall still has to be able to read the server.

`initialize` and `tools/list` require a token — an OAuth server SHOULD answer 401 so
the flow is discovered — which leaves a scanner with nothing to put on a page, and a
listing created empty: no tools, no description.

The card answers that, unauthenticated, and is generated from the live server so it
cannot describe tools the server does not have.
"""
import json
from pathlib import Path

import pytest

from pexafy_mcp import server as server_module
from pexafy_mcp import tooling

PATH = "/.well-known/mcp/server-card.json"
OAUTH_ENV = {
    "PEXAFY_MCP_TRANSPORT": "http",
    "PEXAFY_OAUTH_RESOLVE_URL": "http://django.invalid/oauth/mcp/resolve",
    "MCP_RESOLVE_SECRET": "test-secret",
    "PEXAFY_OAUTH_AS_URL": "https://pexafy.com",
    "PEXAFY_MCP_PUBLIC_URL": "https://mcp.pexafy.com",
}


def _get(app, path):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        return client.get(path)


@pytest.fixture
def served(reload_with_env):
    """The OAuth-guarded server, and the names of the tools it serves."""
    import asyncio

    server = reload_with_env(server_module, OAUTH_ENV)
    mcp = server.build_server()
    return mcp, {tool.name for tool in asyncio.run(mcp.list_tools())}


@pytest.fixture
def card(served):
    mcp, _ = served
    response = _get(mcp.http_app(), PATH)
    assert response.status_code == 200
    return response.json()


def test_the_card_is_readable_without_credentials(card):
    """The whole point: the endpoint that needs a token is not the one describing it."""
    assert card["serverInfo"]["name"]
    assert card["serverInfo"]["version"]


def test_it_declares_the_auth_wall_it_sits_behind(card):
    assert card["authentication"] == {"required": True, "schemes": ["oauth2"]}


def test_an_account_is_optional_when_anonymous_access_is_on(served, monkeypatch):
    """With anonymous access configured every tool also declares `noauth`, so the card
    must not tell a directory that a sign-in comes first."""
    from pexafy_mcp import anonymous

    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", "test-secret")
    mcp, _ = served
    card = _get(mcp.http_app(), PATH).json()
    assert card["authentication"] == {"required": False, "schemes": ["oauth2"]}


def test_it_lists_the_tools_the_server_actually_serves(card, served):
    """Read off the live server, so it follows the configuration: the file tool only
    exists with a thumbnail proxy. Every tool served is on the card, the account link
    and the selection included."""
    _, names = served
    listed = {tool["name"] for tool in card["tools"]}
    assert listed == names
    assert {tooling.PUBLIC_TOOL_NAMES["search_photos"],
            tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]} <= listed


def test_it_carries_what_a_client_lists(reload_with_env, monkeypatch):
    """Tools, resources and prompts, exactly as a connected client receives them —
    order and inlined `$ref`s included — so the card cannot drift from the server. With
    the thumbnail proxy on, so that the file tool and the grid resource are there too."""
    import asyncio

    from fastmcp import Client

    from pexafy_mcp import previews

    server = reload_with_env(server_module, OAUTH_ENV)
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    mcp = server.build_server()
    card = _get(mcp.http_app(), PATH).json()

    async def listed():
        async with Client(mcp) as client:
            return (await client.list_tools(), await client.list_resources(),
                    await client.list_prompts())

    tools, resources, prompts = asyncio.run(listed())

    def dump(items):
        return [item.model_dump(mode="json", exclude_none=True) for item in items]

    assert card["tools"] == dump(tools)
    assert card["resources"] == dump(resources)
    assert card["prompts"] == dump(prompts)
    assert tooling.PUBLIC_TOOL_NAMES["get_photo_file"] in {t["name"] for t in card["tools"]}
    assert card["resources"], "the grid resource is missing"
    assert [prompt["name"] for prompt in card["prompts"]] == ["find_photos"]


def test_reading_it_is_not_logged_as_a_host(served, caplog):
    """A directory reading the card is not an MCP message: observe.py's per-message log
    line would count it as a host listing the tools."""
    mcp, _ = served
    with caplog.at_level("INFO", logger="pexafy.mcp.meta"):
        assert _get(mcp.http_app(), PATH).status_code == 200
    assert not [r for r in caplog.records if r.name == "pexafy.mcp.meta"]


def test_every_listed_tool_carries_a_callable_schema(card):
    """A name and a sentence is not enough for a client to decide it can call the server."""
    for tool in card["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_the_card_says_no_auth_when_the_server_has_none(reload_with_env):
    """Local/stdio use: claiming OAuth there would send a scanner looking for one."""
    server = reload_with_env(server_module, {"PEXAFY_MCP_TRANSPORT": "http"})
    card = _get(server.build_server().http_app(), PATH).json()
    assert card["authentication"] == {"required": False, "schemes": []}


def test_the_display_title_matches_the_registry_manifest():
    """Two listings of the same server should not carry two different names."""
    manifest = json.loads((Path(__file__).resolve().parents[1] / "server.json").read_text())
    assert server_module.SERVER_TITLE == manifest["title"]
