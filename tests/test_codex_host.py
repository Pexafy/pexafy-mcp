"""Codex reads the whole answer: an OpenAI host that is not known to show the grid.

Production's access log, 14 days of MCP requests by User-Agent (measured 2026-10-02):
`openai-mcp/1.0.0`, ChatGPT, 10,835; `openai-mcp/1.0.0 (Codex)`, Codex through OpenAI's
apps platform, 4,439; and Codex CLI, connected directly, as `codex-mcp-client/0.159.x`.
Through the platform Codex names itself as ChatGPT does, and hosts.renders_grid took it
for a host that shows the grid: the text it read named the photographs without a link
(previews.grid_summary), its previews came without an address, and its user had no way
left to see or use them. Told apart now by its User-Agent, or by a `clientInfo` naming
Codex (hosts.is_codex), it reads the whole answer, links included, as in 0.4.12 — unless
it declares MCP Apps itself. It stays an OpenAI host: the 0.4.12 grid's window is still
its own.

The answers are read over the HTTP transport, as Codex reaches the server: the
User-Agent is a header of the request, which an in-process client has none of. Offline:
the API is an httpx.MockTransport.
"""
from __future__ import annotations

import copy
import json
import time

import httpx
import pytest
from fastmcp.apps import UI_EXTENSION_ID, UI_MIME_TYPE
from starlette.testclient import TestClient

from pexafy_mcp import anonymous, compat, hosts, previews, server, tooling

CODEX = "openai-mcp/1.0.0 (Codex)"          # through OpenAI's apps platform
CHATGPT = "openai-mcp/1.0.0"
CODEX_CLI = "codex-mcp-client/0.159.0"      # Codex CLI, connected directly
APPS = {UI_EXTENSION_ID: {"mimeTypes": [UI_MIME_TYPE]}}

THUMB_BASE = "https://thumb.pexafy.com"
SEARCH = tooling.PUBLIC_TOOL_NAMES["search_photos"]
SIZES = ("thumb", "small", "regular", "large", "full")

# Two photographs as the API writes them.
API_PHOTOS = [
    {
        "photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c1",
        "urls": {size: f"https://images.example/1/{size}.jpg" for size in SIZES},
        "width": 4000, "height": 3000, "orientation": "landscape",
        "photographer_full_name": "Jane Doe", "photographer_url": "https://www.pexels.com/@jdoe",
        "source": "Pexels", "license_type": "free",
        "source_image_url": "https://www.pexels.com/photo/1/",
        "alt_description": "red bicycle against a white wall",
        "attribution": {"plain": "Photo by Jane Doe on Pexels (https://pexafy.com/legal/licenses/#pexels)"},
    },
    {
        "photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c2",
        "urls": {size: f"https://images.example/2/{size}.jpg" for size in SIZES},
        "width": 3000, "height": 4500, "orientation": "portrait",
        "photographer_full_name": "Ana Lima", "photographer_url": "https://pixabay.com/users/ana/",
        "source": "Pixabay", "license_type": "free",
        "attribution": {"plain": "Photo by Ana Lima on Pixabay (https://pexafy.com/legal/licenses/#pixabay)"},
    },
]


# ── Who is Codex ──────────────────────────────────────────────────────────────

class _Ctx:
    """A middleware context whose session names the client and says what it shows."""

    def __init__(self, name: str, extensions: dict | None = None):
        caps = type("Caps", (), {"model_extra": {"extensions": extensions} if extensions else {},
                                 "experimental": None})()
        info = type("I", (), {"name": name})()
        params = type("P", (), {"clientInfo": info, "capabilities": caps})()
        session = type("S", (), {"client_params": params})()
        request = type("R", (), {"meta": type("M", (), {"model_extra": {}})()})()
        self.fastmcp_context = type("C", (), {"session": session, "request_context": request})()


@pytest.fixture
def user_agent(monkeypatch):
    """Set the User-Agent of the request in scope, in the headers hosts.py reads."""
    def set_to(value: str) -> None:
        monkeypatch.setattr(anonymous, "_incoming_headers",
                            lambda: {"user-agent": value} if value else {})
    return set_to


@pytest.mark.parametrize("agent", [CODEX, "OpenAI-MCP/1.0.0 (CODEX)", "openai-mcp/1.1.0 (codex)"])
def test_codex_is_told_by_its_user_agent_and_shows_no_grid(user_agent, agent):
    user_agent(agent)
    codex = _Ctx("openai-mcp")
    assert hosts.is_codex(codex)
    assert not hosts.renders_grid(codex)
    # Still an OpenAI host: what compat.py keeps for the apps platform is its own.
    assert hosts.is_openai(codex)
    assert hosts.assistant_name(codex) == "Codex"


@pytest.mark.parametrize("name", ["codex-mcp-client", "Codex"])
def test_a_client_that_names_itself_codex_is_codex(user_agent, name):
    user_agent("")
    context = _Ctx(name)
    assert hosts.is_codex(context) and not hosts.renders_grid(context)
    assert hosts.assistant_name(context) == "Codex"


def test_codex_that_declares_mcp_apps_is_taken_at_its_word(user_agent):
    user_agent(CODEX)
    for name in ("openai-mcp", "codex-mcp-client"):
        context = _Ctx(name, extensions=APPS)
        assert hosts.is_codex(context) and hosts.renders_grid(context), name
        assert hosts.assistant_name(context) == "Codex"


def test_chatgpt_is_not_codex(user_agent):
    # Its own User-Agent, or none at all (stdio, an in-process client): its name tells.
    for agent in (CHATGPT, ""):
        user_agent(agent)
        chatgpt = _Ctx("openai-mcp")
        assert not hosts.is_codex(chatgpt)
        assert hosts.is_openai(chatgpt) and hosts.renders_grid(chatgpt)
        assert hosts.assistant_name(chatgpt) == "ChatGPT"


def test_codex_cli_is_still_a_client_without_the_grid(user_agent):
    user_agent(CODEX_CLI)
    cli = _Ctx("codex-mcp-client")
    assert not hosts.renders_grid(cli)
    # Not the apps platform: no alias, no window, as before.
    assert not hosts.is_openai(cli)


# ── What each one reads, over HTTP ────────────────────────────────────────────

def _answer(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": copy.deepcopy(API_PHOTOS)})


@pytest.fixture
def grid_on(reload_with_env, fake_api, monkeypatch):
    """Production's configuration — the thumbnail proxy, hence the grid — with the API in
    memory, and no 0.4.12 window unless a test opens one."""
    reload_with_env(previews, {"PEXAFY_THUMB_BASE_URL": THUMB_BASE,
                               "PEXAFY_THUMB_HMAC_SECRET": "test-secret"})
    monkeypatch.setattr(compat, "LEGACY_GRID_UNTIL", None)
    fake_api.respond = _answer
    return fake_api


_ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, body: dict, headers: dict, session: str = "") -> tuple[dict, str]:
    """POST one JSON-RPC message; (its answer, the session id)."""
    extra = {"mcp-session-id": session} if session else {}
    response = client.post("/mcp", content=json.dumps(body), headers={**_ACCEPT, **headers, **extra})
    assert response.status_code in (200, 202), response.text
    answer = {}
    if response.text.strip():
        lines = [line[5:] for line in response.text.splitlines() if line.startswith("data:")]
        answer = json.loads(lines[-1] if lines else response.text)
    return answer, response.headers.get("mcp-session-id", session)


def _as(agent: str, name: str, *, apps: bool = False) -> tuple[list[str], dict]:
    """A session over HTTP with `agent` as the User-Agent of every request and `name` as
    its `clientInfo`, MCP Apps declared when `apps`, that lists the tools and runs one
    text search: (the names listed, the search's result)."""
    headers = {"User-Agent": agent}
    with TestClient(server.http_app(server.build_server())) as client:
        _, session = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {"extensions": APPS} if apps else {},
            "clientInfo": {"name": name, "version": "1.0.0"}}}, headers)
        _rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, session)
        listed, _ = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                         headers, session)
        called, _ = _rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": SEARCH, "arguments": {tooling.PUBLIC_QUERY_PARAM: "a red bicycle"}}},
            headers, session)
    assert called["result"]["isError"] is False, called
    return [tool["name"] for tool in listed["result"]["tools"]], called["result"]


def _reads_the_whole_answer(result: dict) -> None:
    """What a client without the grid reads, as in 0.4.12: the answer as JSON, with the
    photographs' links — `urls.regular` and the previews whole — in the text and in
    `structuredContent` alike."""
    text = result["content"][0]["text"]
    for half in (json.loads(text), result["structuredContent"]):
        for photo, sent in zip(half["data"], API_PHOTOS, strict=True):
            pid = sent["photo_id"]
            assert photo["urls"] == {"regular": sent["urls"]["regular"]}
            assert photo["preview_url"].startswith(f"{THUMB_BASE}/{pid}/480w.jpg?e=")
            assert photo["preview_url_large"].startswith(f"{THUMB_BASE}/{pid}/1280w.jpg?s=")
    assert "https://images.example/1/regular.jpg" in text


def _reads_the_grid_summary(result: dict) -> None:
    """What a client that shows the grid reads: the photographs named without a link,
    and a `structuredContent` with no image address left in it."""
    text = result["content"][0]["text"]
    assert text.startswith("2 photographs for “a red bicycle” are on the person's screen")
    assert "http" not in text
    for photo, sent in zip(result["structuredContent"]["data"], API_PHOTOS, strict=True):
        assert "urls" not in photo
        assert photo["preview_url"].startswith(f"{sent['photo_id']}/480w.jpg?e=")
    half = json.dumps(result["structuredContent"])
    assert THUMB_BASE not in half and "images.example" not in half


def test_codex_reads_the_whole_answer_with_its_links(grid_on):
    _, result = _as(CODEX, "openai-mcp")
    _reads_the_whole_answer(result)
    assert result["_meta"]["pexafy/host"] == {"assistant": "Codex"}
    # The grid's own copy stays in `_meta`, as for every host.
    assert set(result["_meta"][previews.PREVIEW_META_KEY]) == {p["photo_id"] for p in API_PHOTOS}


def test_codex_that_declares_mcp_apps_gets_the_grid(grid_on):
    _, result = _as(CODEX, "openai-mcp", apps=True)
    _reads_the_grid_summary(result)
    assert result["_meta"]["pexafy/host"] == {"assistant": "Codex"}


def test_chatgpt_is_unchanged(grid_on):
    names, result = _as(CHATGPT, "openai-mcp")
    _reads_the_grid_summary(result)
    assert result["_meta"]["pexafy/host"] == {"assistant": "ChatGPT"}
    assert compat.SIMILAR_TOOL not in names


def test_codex_cli_still_reads_the_whole_answer(grid_on):
    names, result = _as(CODEX_CLI, "codex-mcp-client")
    _reads_the_whole_answer(result)
    assert compat.SIMILAR_TOOL not in names
    # What a grid would say, were one shown: the photos go to Codex.
    assert result["_meta"]["pexafy/host"] == {"assistant": "Codex"}


def test_codex_is_listed_the_current_tools_only(grid_on):
    names, _ = _as(CODEX, "openai-mcp")
    assert compat.SIMILAR_TOOL not in names
    assert names == [n for n in server.TOOL_ORDER if n in names], names


def test_the_0_4_12_window_still_applies_to_codex(grid_on, monkeypatch):
    monkeypatch.setattr(compat, "LEGACY_GRID_UNTIL", time.time() + 3600)
    _, result = _as(CODEX, "openai-mcp")
    structured = result["structuredContent"]
    # What the 0.4.12 grid reads is put back: `urls` in every size, the fields 1.0.0
    # prunes, the link under the grid.
    for photo, sent in zip(structured["data"], API_PHOTOS, strict=True):
        assert photo["urls"] == sent["urls"]
        assert photo["photographer_url"] == sent["photographer_url"]
    assert structured["cta"] == result["_meta"]["pexafy/cta"]
    # The text is the whole answer, written before anything was put back.
    body = json.loads(result["content"][0]["text"])
    assert body["data"][0]["urls"] == {"regular": API_PHOTOS[0]["urls"]["regular"]}
    # Codex CLI does not come through the apps platform: no window for it.
    _, cli = _as(CODEX_CLI, "codex-mcp-client")
    assert "cta" not in cli["structuredContent"]
    assert cli["structuredContent"]["data"][0]["urls"] == {"regular": API_PHOTOS[0]["urls"]["regular"]}
