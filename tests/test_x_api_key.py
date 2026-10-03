"""A Pexafy key sent in `x-api-key` (README, "Any other MCP client") is the caller's
own credential, whatever else is switched on.

With OAuth on, FastMCP only reads `Authorization`. Anonymous access on, such a caller
was handed the synthetic bearer and searched with the SERVICE key and an anonymous
principal (anonymous allowance, "create an account"); anonymous access off, it got a
401. Offline: ASGI in process, the API is an httpx.MockTransport.
"""
from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from pexafy_mcp import anonymous, server, tooling

CALLER_KEY = "pexafy_api_THE_CALLERS_OWN_KEY"


async def _headers_after_asgi(headers):
    seen = {}

    async def downstream(scope, receive, send):
        seen.update(dict(scope.get("headers") or []))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(_message):
        return None

    await anonymous.AllowAnonymous(downstream)(
        {"type": "http", "headers": headers, "path": "/mcp", "method": "POST"}, None, send)
    return seen


@pytest.mark.parametrize("anonymous_on", [True, False])
async def test_the_key_is_moved_where_the_auth_layer_reads_it(monkeypatch, anonymous_on):
    monkeypatch.setattr(anonymous, "ENABLED", anonymous_on)
    monkeypatch.setattr(anonymous, "SECRET", "test-secret")
    headers = await _headers_after_asgi([
        (b"cf-connecting-ip", b"203.0.113.5"), (b"x-api-key", CALLER_KEY.encode())])
    assert headers[b"authorization"] == f"Bearer {CALLER_KEY}".encode()


async def test_an_authorization_header_still_wins(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", "test-secret")
    headers = await _headers_after_asgi([
        (b"authorization", b"Bearer oauth-token"), (b"x-api-key", CALLER_KEY.encode())])
    assert headers[b"authorization"] == b"Bearer oauth-token"


async def test_a_header_that_is_not_a_pexafy_key_changes_nothing(monkeypatch):
    """A gateway's own `x-api-key` is not a Pexafy credential: it must not turn an
    anonymous caller into a refused one."""
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", "test-secret")
    headers = await _headers_after_asgi([
        (b"cf-connecting-ip", b"203.0.113.5"), (b"x-api-key", b"gateway-key-123")])
    assert headers[b"authorization"] == f"Bearer {anonymous.SYNTHETIC_BEARER}".encode()


@pytest.mark.parametrize("anonymous_on", [True, False])
def test_the_api_is_called_with_the_callers_key_and_no_principal(monkeypatch, anonymous_on):
    monkeypatch.setattr(anonymous, "ENABLED", anonymous_on)
    monkeypatch.setattr(anonymous, "SECRET", "test-secret")
    monkeypatch.setattr(anonymous, "SOURCES", ["ip"])
    monkeypatch.setattr(server, "OAUTH_ENABLED", True)
    monkeypatch.setattr(server, "OAUTH_RESOLVE_URL", "http://resolve.invalid/oauth/mcp/resolve")
    monkeypatch.setattr(server, "OAUTH_RESOLVE_SECRET", "resolve-secret")
    monkeypatch.setattr(server, "OAUTH_AS_URL", "http://as.invalid")
    monkeypatch.setattr(server, "MCP_PUBLIC_URL", "http://testserver")
    seen = []

    def api(request):
        seen.append({"x-api-key": request.headers.get("x-api-key", ""),
                     "principal": anonymous.PRINCIPAL_HEADER in request.headers})
        return httpx.Response(200, json={"success": True, "data": []},
                              headers={"content-type": "application/json"})

    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(api))
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json",
               "X-Forwarded-For": "203.0.113.7", "x-api-key": CALLER_KEY}

    def rpc(client, body, session=None):
        extra = {"mcp-session-id": session} if session else {}
        return client.post("/mcp", content=json.dumps(body), headers={**headers, **extra})

    with TestClient(server.http_app(server.build_server())) as client:
        r = rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}}})
        assert r.status_code == 200, r.text
        session = r.headers.get("mcp-session-id")
        rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
        r = rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": tooling.PUBLIC_TOOL_NAMES["search_photos"],
            "arguments": {tooling.PUBLIC_QUERY_PARAM: "a red bicycle"}}}, session)
        assert r.status_code == 200, r.text

    assert seen == [{"x-api-key": CALLER_KEY, "principal": False}]
