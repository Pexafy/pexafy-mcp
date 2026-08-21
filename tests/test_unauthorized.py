"""The 401 never named the one way in that would have worked for this visitor.

20 August 2026, four residential IPs over two hours: 151 requests to /mcp, 50 of
them carrying a credential, 66 matching token rejections on the Django side, and
not one `.well-known` document ever fetched — so his client does not speak OAuth
at all. He got two answers, and neither helped: an empty body when he sent
nothing, and 301 bytes of OAuth advice when he sent something ("clear the stored
tokens and reconnect"), which is a dead end for a client with no tokens to
clear. This server accepts a plain Pexafy API key as the bearer. Nothing said so.

These tests hold the two things that make the fix worth having: both bodies say
something, and each says the right thing for the situation the caller is in —
without disturbing the header an MCP client actually follows.
"""
from __future__ import annotations

import importlib
import json
import os

import pytest
from starlette.testclient import TestClient

OAUTH_ENV = {
    "PEXAFY_MCP_TRANSPORT": "http",
    "PEXAFY_OAUTH_RESOLVE_URL": "http://django/oauth/mcp/resolve",
    "MCP_RESOLVE_SECRET": "test-secret",
    "PEXAFY_OAUTH_AS_URL": "https://pexafy.com",
    "PEXAFY_MCP_PUBLIC_URL": "https://mcp.pexafy.com",
}

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {"protocolVersion": "2025-11-25", "capabilities": {},
               "clientInfo": {"name": "test", "version": "1"}},
}


@pytest.fixture
def guarded_app(monkeypatch):
    """The server as deployed: OAuth on, and the middleware main() installs."""
    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    for key, value in OAUTH_ENV.items():
        monkeypatch.setenv(key, value)

    from pexafy_mcp import server

    importlib.reload(server)
    return server.build_server().http_app(middleware=server.HTTP_MIDDLEWARE)


def _post(app, headers=None):
    with TestClient(app) as client:
        return client.post("/mcp", json=INITIALIZE, headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **(headers or {}),
        })


def test_the_401_is_no_longer_empty(guarded_app):
    response = _post(guarded_app)
    assert response.status_code == 401
    assert response.content, "the body a debugging human reads was empty"


def test_a_caller_with_no_credential_is_told_both_ways_in(guarded_app):
    body = _post(guarded_app).json()
    assert body["error"] == "unauthorized"
    assert "OAuth" in body["error_description"]
    assert "pexafy_api_" in body["error_description"], "the API key path must be named"
    assert body["keys_url"] == "https://pexafy.com/dashboard/api-keys"


def test_a_refused_credential_gets_the_other_message(guarded_app):
    """"You sent nothing" and "what you sent was refused" are different problems.

    The SDK already answers this case with 301 bytes of OAuth advice — clear the
    stored tokens and reconnect — which is a dead end for the client that has no
    tokens to clear. The machine-readable code it chose is kept; the prose is
    replaced by one that names both ways in.
    """
    response = _post(guarded_app, {"Authorization": "Bearer not-a-real-token"})
    body = response.json()
    assert body["error"] == "invalid_token", "the SDK's error code must survive"
    assert "expired" in body["error_description"], "an expired OAuth token is the likely cause"
    assert "pexafy_" in body["error_description"], "so is a key pasted in the wrong shape"
    assert body["keys_url"] == "https://pexafy.com/dashboard/api-keys"


def test_the_www_authenticate_header_still_leads_the_oauth_chain(guarded_app):
    """The body is for humans. The header is what an MCP client follows — untouched."""
    response = _post(guarded_app)
    header = response.headers["www-authenticate"]
    assert "resource_metadata=" in header
    assert "oauth-protected-resource" in header


def test_content_length_matches_the_body_we_substituted(guarded_app):
    response = _post(guarded_app)
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["content-type"] == "application/json"


def test_a_401_that_already_explains_itself_is_left_alone(monkeypatch):
    """/metrics guards itself with a token and answers with its own body."""
    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PEXAFY_MCP_TRANSPORT", "http")
    monkeypatch.setenv("PEXAFY_METRICS_TOKEN", "s3cret")

    from pexafy_mcp import server

    importlib.reload(server)
    app = server.build_server().http_app(middleware=server.HTTP_MIDDLEWARE)

    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 401
    assert response.text == "Unauthorized", "the middleware overwrote a body it should not touch"


def test_successful_traffic_is_untouched(monkeypatch):
    """The middleware sees every response; it must only ever rewrite empty 401s."""
    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PEXAFY_MCP_TRANSPORT", "http")

    from pexafy_mcp import server

    importlib.reload(server)
    app = server.build_server().http_app(middleware=server.HTTP_MIDDLEWARE)

    with TestClient(app) as client:
        handshake = client.post("/mcp", json=INITIALIZE, headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        health = client.get("/health")

    assert handshake.status_code == 200
    assert handshake.headers.get("mcp-session-id")
    assert json.loads(health.text)["status"] == "ok"
