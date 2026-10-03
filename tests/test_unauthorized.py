"""The 401 has to name the two ways in, not just one.

A client that does not speak OAuth gets no help from the spec's answer: an empty
body when it sends nothing, and OAuth advice ("clear the stored tokens and
reconnect") when it sends a credential the SDK refuses — a dead end for a client
that holds no tokens. This server also accepts a plain Pexafy API key as the bearer,
and neither answer said so.

These tests hold the two things that make the fix worth having: both bodies say
something, and each says the right thing for the situation the caller is in, without
disturbing the header an MCP client actually follows.
"""
from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from pexafy_mcp import limits, unauthorized
from pexafy_mcp import server as server_module

KEYS_URL = "https://pexafy.com/dashboard/api-keys/"
CREATE_KEY_URL = KEYS_URL + "create/"

OAUTH_ENV = {
    "PEXAFY_MCP_TRANSPORT": "http",
    "PEXAFY_OAUTH_RESOLVE_URL": "http://django.invalid/oauth/mcp/resolve",
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
def resolve_calls():
    """The requests Django's resolve endpoint received."""
    return []


@pytest.fixture
def guarded_app(reload_with_env, resolve_calls):
    """The server as deployed: OAuth on, and the middleware main() installs.

    Django's resolve endpoint is answered in memory, and it refuses every token.
    """
    server = reload_with_env(server_module, OAUTH_ENV)

    def django_refuses(request):
        resolve_calls.append(request)
        return httpx.Response(401, json={"error": "invalid_token"})

    class Verifier(server.PexafyResolveVerifier):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault(
                "client", httpx.AsyncClient(transport=httpx.MockTransport(django_refuses)))
            super().__init__(*args, **kwargs)

    # Undone with the rest of the module's globals by reload_with_env.
    server.PexafyResolveVerifier = Verifier
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
    assert body["keys_url"] == CREATE_KEY_URL


def test_a_caller_with_no_key_is_sent_to_the_form_that_makes_one(guarded_app):
    """The list of keys is one click short for a caller who has none: the sentence
    links the creation form and names the client origin to pick there."""
    description = _post(guarded_app).json()["error_description"]
    assert "Create an MCP Agent key at https://pexafy.com/dashboard/api-keys/create/." in description


def test_a_refused_credential_gets_the_other_message(guarded_app, resolve_calls):
    """"You sent nothing" and "what you sent was refused" are different problems.

    The SDK already answers this case with 301 bytes of OAuth advice — clear the
    stored tokens and reconnect — which is a dead end for the client that has no
    tokens to clear. The machine-readable code it chose is kept; the prose is
    replaced by one that names both ways in.
    """
    response = _post(guarded_app, {"Authorization": "Bearer not-a-real-token"})
    body = response.json()
    # Refused by the resolve step, not lost on the way to it.
    [resolve] = resolve_calls
    assert resolve.headers["authorization"] == "Bearer not-a-real-token"
    assert body["error"] == "invalid_token", "the SDK's error code must survive"
    assert "expired" in body["error_description"], "an expired OAuth token is the likely cause"
    assert "pexafy_" in body["error_description"], "so is a key pasted in the wrong shape"
    assert f"an MCP Agent key at {CREATE_KEY_URL}." in body["error_description"]
    assert body["keys_url"] == CREATE_KEY_URL


def test_a_bearer_that_is_not_ascii_is_refused_like_any_other(guarded_app, resolve_calls):
    """It used to reach `compare_digest` as a str and raise TypeError: a 500."""
    response = _post(guarded_app, {"Authorization": b"Bearer t\xc3\xa9l\xc3\xa9phone"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert resolve_calls == [], "a credential nobody could have issued is not worth resolving"


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


def test_a_401_that_already_explains_itself_is_left_alone(reload_with_env):
    """/metrics guards itself with a token and answers with its own body."""
    server = reload_with_env(server_module, {
        "PEXAFY_MCP_TRANSPORT": "http", "PEXAFY_METRICS_TOKEN": "s3cret"})
    app = server.build_server().http_app(middleware=server.HTTP_MIDDLEWARE)

    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 401
    assert response.text == "Unauthorized", "the middleware overwrote a body it should not touch"


def test_successful_traffic_is_untouched(reload_with_env):
    """The middleware sees every response; it must only ever rewrite empty 401s."""
    server = reload_with_env(server_module, {"PEXAFY_MCP_TRANSPORT": "http"})
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


def test_the_keys_link_follows_the_page_the_key_limit_message_names(reload_with_env):
    """One setting: the key-limit message names the list, the 401 links its creation
    form. The list keeps its canonical form: without the trailing slash Django answers
    301, one more hop through the client for a slash we dropped."""
    assert unauthorized.KEYS_URL == limits.CONNECTORS_URL == KEYS_URL
    assert unauthorized.CREATE_KEY_URL == CREATE_KEY_URL
    elsewhere = "https://preprod.example/dashboard/api-keys/"
    reload_with_env(limits, {"PEXAFY_CONNECTORS_URL": elsewhere})
    reloaded = reload_with_env(unauthorized)
    assert reloaded.KEYS_URL == elsewhere
    body = json.loads(reloaded._rewrite({"headers": []}, b""))
    assert body["keys_url"] == f"{elsewhere}create/", "the form follows the list"
    assert f"at {elsewhere}create/." in body["error_description"]
