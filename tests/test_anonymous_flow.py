"""The anonymous path end to end: no credential in, a signed principal out.

Three joints are tested here because each of them fails silently:

  * `AllowAnonymous` must run OUTSIDE FastMCP's auth middleware, which answers 401 on a
    missing `Authorization` header before any of our code is reached. If it is placed
    wrong, anonymous access simply never happens and the only symptom is a 401.
  * The synthetic bearer must never be forwarded as an API key. It looks like a
    credential to the one line of `_forward_client_key` that reads the header.
  * A caller who DOES present a credential must come out unchanged — no principal, no
    second answer to "who is this".

    pytest tests/test_anonymous_flow.py -v
"""
from __future__ import annotations

import httpx
import pytest

from pexafy_mcp import anonymous, server, tooling
from pexafy_mcp.auth import ANONYMOUS_CLAIM, API_KEY_CLAIM, PexafyResolveVerifier

SECRET = "test-shared-secret"


class _Request:
    """Just enough of httpx.Request for the outgoing hook."""

    def __init__(self, url="http://localhost:8000/api/v1/search/photos"):
        self.headers: dict[str, str] = {}
        self.url = httpx.URL(url)


# ── AllowAnonymous (ASGI) ────────────────────────────────────────────────────

async def _run_asgi(app, headers: list[tuple[bytes, bytes]]):
    seen = {}

    async def downstream(scope, receive, send):
        seen["headers"] = dict(scope.get("headers") or [])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = anonymous.AllowAnonymous(downstream)
    await middleware(
        {"type": "http", "headers": headers, "path": "/mcp", "method": "POST"},
        None,
        lambda message: _noop(),
    )
    return seen["headers"]


async def _noop():
    return None


@pytest.mark.asyncio
async def test_a_credential_less_request_is_given_the_synthetic_bearer(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    headers = await _run_asgi(
        None, [(b"host", b"mcp.pexafy.com"), (b"cf-connecting-ip", b"203.0.113.5")]
    )
    assert headers[b"authorization"] == f"Bearer {anonymous.SYNTHETIC_BEARER}".encode()


@pytest.mark.asyncio
async def test_a_caller_nothing_can_identify_is_left_to_the_401(monkeypatch):
    """Claude.ai: a shared network and no host-supplied id. The 401 FastMCP then
    answers is what makes its client offer the OAuth sign-in."""
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    headers = await _run_asgi(
        None, [(b"host", b"mcp.pexafy.com"), (b"cf-connecting-ip", b"160.79.104.9")]
    )
    assert b"authorization" not in headers


@pytest.mark.asyncio
async def test_an_existing_authorization_is_left_alone(monkeypatch):
    """An OAuth session and a client-supplied key must reach the server untouched."""
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    headers = await _run_asgi(None, [(b"authorization", b"Bearer real-oauth-token")])
    assert headers[b"authorization"] == b"Bearer real-oauth-token"


@pytest.mark.asyncio
async def test_it_is_inert_when_anonymous_access_is_off(monkeypatch):
    """Deploying this version changes nothing until someone turns it on."""
    monkeypatch.setattr(anonymous, "ENABLED", False)
    headers = await _run_asgi(
        None, [(b"host", b"mcp.pexafy.com"), (b"cf-connecting-ip", b"203.0.113.5")]
    )
    assert b"authorization" not in headers


@pytest.mark.asyncio
async def test_it_is_inert_without_a_secret(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", "")
    headers = await _run_asgi(
        None, [(b"host", b"mcp.pexafy.com"), (b"cf-connecting-ip", b"203.0.113.5")]
    )
    assert b"authorization" not in headers


# ── The verifier ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_verifier_accepts_the_synthetic_bearer_and_mints_no_key():
    verifier = PexafyResolveVerifier("http://unused", "secret")
    token = await verifier.verify_token(anonymous.SYNTHETIC_BEARER)
    assert token is not None
    assert token.claims.get(ANONYMOUS_CLAIM) is True
    assert API_KEY_CLAIM not in token.claims, "an anonymous caller carries no user key"
    assert token.scopes == ["read"]


@pytest.mark.asyncio
async def test_a_guessed_bearer_is_not_accepted():
    """It is a per-process nonce; anything else must go down the normal OAuth path."""
    verifier = PexafyResolveVerifier("http://unused", "secret")

    async def fail(*a, **kw):
        raise httpx.ConnectError("resolution attempted, as it should be")

    verifier._client.post = fail
    assert await verifier.verify_token("anon:guessed") is None


@pytest.mark.asyncio
async def test_a_real_api_key_still_takes_the_key_path():
    verifier = PexafyResolveVerifier("http://unused", "secret")
    token = await verifier.verify_token("pexafy_api_whatever")
    assert token.claims[API_KEY_CLAIM] == "pexafy_api_whatever"
    assert ANONYMOUS_CLAIM not in token.claims


# ── The outgoing hook ────────────────────────────────────────────────────────

class _Token:
    def __init__(self, claims):
        self.claims = claims
        self.client_id = "test"


@pytest.mark.asyncio
async def test_an_anonymous_call_carries_a_principal_and_no_key(monkeypatch):
    monkeypatch.setattr(server, "get_access_token", lambda: _Token({ANONYMOUS_CLAIM: True}))
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    identity = anonymous.Identity("sub_1", "openai-subject", "chatgpt", True)
    reset = anonymous.current.set(identity)
    try:
        request = _Request()
        await server._forward_client_key(request)
    finally:
        anonymous.current.reset(reset)

    assert anonymous.PRINCIPAL_HEADER in request.headers
    assert "x-api-key" not in request.headers, (
        "the synthetic bearer must never be forwarded as an API key"
    )


@pytest.mark.asyncio
async def test_an_anonymous_call_with_no_identity_sends_no_principal(monkeypatch, caplog):
    """The API then refuses the shared service key on its own — one key must not become
    one shared budget."""
    monkeypatch.setattr(server, "get_access_token", lambda: _Token({ANONYMOUS_CLAIM: True}))
    reset = anonymous.current.set(None)
    try:
        request = _Request()
        await server._forward_client_key(request)
    finally:
        anonymous.current.reset(reset)

    assert anonymous.PRINCIPAL_HEADER not in request.headers
    assert "x-api-key" not in request.headers


@pytest.mark.asyncio
async def test_an_oauth_user_is_untouched(monkeypatch):
    """Non-regression: the resolved key path must not acquire a principal."""
    monkeypatch.setattr(
        server, "get_access_token", lambda: _Token({API_KEY_CLAIM: "pexafy_api_user_key"})
    )
    identity = anonymous.Identity("sub_1", "openai-subject", "chatgpt", True)
    reset = anonymous.current.set(identity)
    try:
        request = _Request()
        await server._forward_client_key(request)
    finally:
        anonymous.current.reset(reset)

    assert request.headers["x-api-key"] == "pexafy_api_user_key"
    assert anonymous.PRINCIPAL_HEADER not in request.headers, (
        "an identified user must not also be given an anonymous identity"
    )


@pytest.mark.asyncio
async def test_a_client_supplied_key_is_untouched(monkeypatch):
    monkeypatch.setattr(server, "get_access_token", lambda: None)
    monkeypatch.setattr(server, "get_http_headers", lambda include=None: {"x-api-key": "pexafy_k"})
    request = _Request()
    await server._forward_client_key(request)
    assert request.headers["x-api-key"] == "pexafy_k"
    assert anonymous.PRINCIPAL_HEADER not in request.headers


# ── The MCP middleware ───────────────────────────────────────────────────────

class _Context:
    method = "tools/call"
    fastmcp_context = None
    message = None


@pytest.mark.asyncio
async def test_the_middleware_resolves_only_for_anonymous_callers(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    monkeypatch.setattr(anonymous, "SOURCES", ["ip"])
    monkeypatch.setattr(
        anonymous, "_incoming_headers",
        lambda: {"authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
                 "cf-connecting-ip": "203.0.113.9"},
    )
    seen = {}

    async def call_next(context):
        seen["identity"] = anonymous.current.get()
        return "ok"

    assert await anonymous.ResolveIdentity().on_message(_Context(), call_next) == "ok"
    assert seen["identity"].subject == "203.0.113.9"
    assert anonymous.current.get() is None, "the identity must not leak past the request"


@pytest.mark.asyncio
async def test_the_middleware_skips_a_caller_with_a_real_credential(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    monkeypatch.setattr(
        anonymous, "_incoming_headers",
        lambda: {"authorization": "Bearer a-real-oauth-token", "cf-connecting-ip": "203.0.113.9"},
    )
    seen = {}

    async def call_next(context):
        seen["identity"] = anonymous.current.get()
        return "ok"

    await anonymous.ResolveIdentity().on_message(_Context(), call_next)
    assert seen["identity"] is None


@pytest.mark.asyncio
async def test_a_resolution_failure_never_costs_a_search(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    monkeypatch.setattr(
        anonymous, "_incoming_headers",
        lambda: {"authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}"},
    )

    async def boom(*a, **kw):
        raise RuntimeError("feed exploded")

    monkeypatch.setattr(anonymous, "resolve", boom)

    async def call_next(context):
        return "served anyway"

    assert await anonymous.ResolveIdentity().on_message(_Context(), call_next) == "served anyway"


# ── The whole server still builds ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_server_builds_with_anonymous_access_on(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    mcp = server.build_server()
    names = {tool.name for tool in await mcp.list_tools()}
    assert tooling.PUBLIC_TOOL_NAMES["search_photos"] in names


def _authenticated_server(monkeypatch):
    """A server built WITH the OAuth resource server, as production runs it.

    Without it `build_server` installs no auth at all and every request answers 200 —
    which would make these three tests pass while proving nothing. Django is never
    called: the tests never present a real token."""
    monkeypatch.setattr(server, "OAUTH_ENABLED", True)
    monkeypatch.setattr(server, "OAUTH_RESOLVE_URL", "http://unused.invalid/resolve")
    monkeypatch.setattr(server, "OAUTH_RESOLVE_SECRET", "unused")
    # The RFC 9728 metadata document is built at construction and validates these.
    monkeypatch.setattr(server, "OAUTH_AS_URL", "https://as.invalid")
    monkeypatch.setattr(server, "MCP_PUBLIC_URL", "https://mcp.invalid")
    return server.build_server()


def test_a_credential_less_request_is_not_refused_by_the_real_stack(monkeypatch):
    """The test that would have caught the deploy bug.

    An earlier version passed AllowAnonymous in `HTTP_MIDDLEWARE` and asserted it sat
    first in that list. It did — and it still ran too late, because FastMCP appends
    custom middleware AFTER the authentication it installs itself. The assertion was
    about our list; the failure was about FastMCP's stack. So this drives a request
    through the assembled app instead and checks the only thing that matters: a request
    with no Authorization header is not answered 401.
    """
    from starlette.testclient import TestClient

    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    app = server.http_app(_authenticated_server(monkeypatch))
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}}},
            headers={"Accept": "application/json, text/event-stream",
                     "CF-Connecting-IP": "203.0.113.9"},
        )
    assert resp.status_code != 401, (
        "a credential-less request is still being refused — AllowAnonymous is not "
        "outside the auth middleware"
    )


def test_a_caller_nothing_can_identify_still_gets_the_401(monkeypatch):
    """The other half: Claude.ai must keep meeting the 401 that offers OAuth."""
    from starlette.testclient import TestClient

    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    app = server.http_app(_authenticated_server(monkeypatch))
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}}},
            headers={"Accept": "application/json, text/event-stream",
                     "CF-Connecting-IP": "160.79.104.9"},
        )
    assert resp.status_code == 401


def test_anonymous_access_off_leaves_the_app_exactly_as_it_was(monkeypatch):
    monkeypatch.setattr(anonymous, "ENABLED", False)
    from starlette.testclient import TestClient

    app = server.http_app(_authenticated_server(monkeypatch))
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}}},
            headers={"Accept": "application/json, text/event-stream",
                     "CF-Connecting-IP": "203.0.113.9"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_authorization_header_is_actually_asked_for(monkeypatch):
    """The bug this guards: `get_http_headers()` hides `authorization` by default, so
    resolution saw no synthetic bearer, decided the caller was authenticated, and
    resolved nobody. Every anonymous tool call then failed at the API with
    PRINCIPAL_REQUIRED — with the feature configured correctly on both sides."""
    captured = {}

    def fake(include=None, include_all=False):
        captured["include"] = set(include or ())
        return {"authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}"}

    monkeypatch.setattr("fastmcp.server.dependencies.get_http_headers", fake)
    headers = anonymous._incoming_headers()

    assert "authorization" in captured["include"], (
        "authorization must be requested explicitly or it is filtered out"
    )
    assert headers["authorization"].endswith(anonymous.SYNTHETIC_BEARER)
    assert anonymous._is_anonymous_request() is True


@pytest.mark.asyncio
async def test_the_headers_identity_needs_are_all_requested():
    """Each of these is a source's only input; a missing one silently disables it."""
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-openai-subject",
                   "mcp-session-id"):
        assert header in anonymous._HEADERS_NEEDED
