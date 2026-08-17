"""The protected-resource metadata has to be findable by clients that guess wrong.

RFC 9728 §3.1 puts the document under the resource's own path —
`/.well-known/oauth-protected-resource/mcp` here — and that is what FastMCP
registers. But a client that probes the bare `/.well-known/oauth-protected-resource`
and gets a 404 concludes the server has no authentication at all. Glama did
exactly that: it listed this connector as "No Auth", then failed its connection
test against a server that had answered 401 with a `WWW-Authenticate` header
pointing straight at the real document.

So both paths must answer, with the same bytes.
"""
import os

import pytest

OAUTH_ENV = {
    "PEXAFY_MCP_TRANSPORT": "http",
    "PEXAFY_OAUTH_RESOLVE_URL": "http://django/oauth/mcp/resolve",
    "MCP_RESOLVE_SECRET": "test-secret",
    "PEXAFY_OAUTH_AS_URL": "https://pexafy.com",
    "PEXAFY_MCP_PUBLIC_URL": "https://mcp.pexafy.com",
}

ROOT = "/.well-known/oauth-protected-resource"
CANONICAL = "/.well-known/oauth-protected-resource/mcp"


@pytest.fixture
def oauth_app(monkeypatch):
    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    for key, value in OAUTH_ENV.items():
        monkeypatch.setenv(key, value)

    import importlib

    from pexafy_mcp import server

    importlib.reload(server)
    return server.build_server().http_app()


def _get(app, path):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        return client.get(path)


def test_canonical_path_serves_the_metadata(oauth_app):
    assert _get(oauth_app, CANONICAL).status_code == 200


def test_well_known_root_serves_it_too(oauth_app):
    """The path a client guesses when it does not implement the path insertion."""
    assert _get(oauth_app, ROOT).status_code == 200


def test_both_paths_serve_the_same_document(oauth_app):
    """One handler, two mounts — they cannot drift apart."""
    assert _get(oauth_app, ROOT).json() == _get(oauth_app, CANONICAL).json()


def test_the_metadata_points_at_the_authorization_server(oauth_app):
    body = _get(oauth_app, ROOT).json()
    assert body["resource"] == "https://mcp.pexafy.com/mcp"
    assert body["authorization_servers"] == ["https://pexafy.com/"]
    assert body["scopes_supported"] == ["read"]


def test_the_openai_challenge_is_absent_until_a_token_is_configured(oauth_app):
    """A 404 is the honest answer. Serving an empty body would let a directory's
    ownership check pass against nothing."""
    assert _get(oauth_app, "/.well-known/openai-apps-challenge").status_code == 404


def test_the_openai_challenge_returns_the_bare_token(monkeypatch):
    """OpenAI's check expects the token itself — no JSON, no wrapper."""
    import importlib
    import os

    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    for key, value in OAUTH_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENAI_APPS_CHALLENGE", "token-abc123")

    from pexafy_mcp import server

    importlib.reload(server)
    response = _get(server.build_server().http_app(), "/.well-known/openai-apps-challenge")
    assert response.status_code == 200
    assert response.text == "token-abc123"
    assert response.headers["content-type"].startswith("text/plain")
