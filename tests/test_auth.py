"""The OAuth token verifier against the answers Django's resolve endpoint can give.

Django is an httpx.MockTransport: nothing leaves the process.
"""
from __future__ import annotations

import httpx
import pytest

from pexafy_mcp import observe
from pexafy_mcp.auth import API_KEY_CLAIM, QUOTA_MESSAGE_CLAIM, PexafyResolveVerifier


def _verifier(respond, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return respond(request)

    return PexafyResolveVerifier(
        "http://django.invalid/oauth/mcp/resolve", "resolve-secret",
        not_oauth_tokens=["metrics-token"],
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("status", [200, 403])
@pytest.mark.parametrize("body", [
    b"<html>502 Bad Gateway</html>",
    b'["not", "an", "object"]',
    b"",
])
async def test_a_body_that_is_not_a_json_object_is_a_refusal_not_a_crash(status, body):
    verifier = _verifier(lambda request: httpx.Response(status, content=body))
    assert await verifier.verify_token("an-oauth-token") is None


async def test_a_resolved_token_carries_the_users_key():
    verifier = _verifier(lambda request: httpx.Response(
        200, json={"api_key": "pexafy_api_user", "email": "jane@example.com", "scope": "read"}))
    token = await verifier.verify_token("an-oauth-token")
    assert token.claims[API_KEY_CLAIM] == "pexafy_api_user"
    assert token.scopes == ["read"]


async def test_a_plan_limit_lets_the_caller_in_with_the_message():
    verifier = _verifier(lambda request: httpx.Response(403, json={
        "error": "key_provisioning_denied", "email": "jane@example.com",
        "context": {"plan_label": "Free", "max": 1}}))
    token = await verifier.verify_token("an-oauth-token")
    assert "1 API key" in token.claims[QUOTA_MESSAGE_CLAIM]


async def test_a_plan_limit_with_a_malformed_context_still_answers():
    verifier = _verifier(lambda request: httpx.Response(403, json={
        "error": "key_provisioning_denied", "context": ["unexpected"]}))
    token = await verifier.verify_token("an-oauth-token")
    assert "has reached its API key limit" in token.claims[QUOTA_MESSAGE_CLAIM]


@pytest.mark.parametrize("token", ["tokén", "pexafy_api_é", "metrics-tokén", "\u00e9" * 40])
async def test_a_bearer_that_is_not_ascii_is_refused_without_asking_django(token):
    """compare_digest raises TypeError on a non-ASCII str, and httpx cannot put one in
    a header: either way the auth middleware answered 500 instead of 401."""
    calls: list[httpx.Request] = []
    verifier = _verifier(lambda request: httpx.Response(200, json={"api_key": "k"}), calls)
    assert await verifier.verify_token(token) is None
    assert calls == []


@pytest.mark.parametrize("status, body", [
    (200, {"api_key": "pexafy_api_user", "email": "jane@example.com"}),
    (403, {"error": "key_provisioning_denied", "email": "jane@example.com"}),
])
async def test_no_email_token_or_key_reaches_the_log(caplog, status, body):
    """These lines are written on every OAuth request, at INFO."""
    verifier = _verifier(lambda request: httpx.Response(status, json=body))
    with caplog.at_level("DEBUG", logger="pexafy_mcp.auth"):
        assert await verifier.verify_token("the-oauth-token") is not None
    assert caplog.records, "nothing was logged at all"
    for secret in ("jane@example.com", "the-oauth-token", "pexafy_api_user", "resolve-secret"):
        assert secret not in caplog.text
    assert observe.email_digest("jane@example.com") in caplog.text, "one user must stay countable"
    # …by a keyed digest: a plain hash is the address to anyone holding the accounts.
    assert observe.digest("jane@example.com") not in caplog.text
