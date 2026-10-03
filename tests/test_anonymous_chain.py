"""One real tool call, from an anonymous caller, all the way to the outgoing request.

The other files test each joint. This one tests that a `tools/call` arriving with no
credential comes out the far side as an HTTP request to the Pexafy API carrying a
signed principal and the service key — through the actual server, the actual middleware
chain, and the actual outgoing hook, with only the network replaced.

That distinction matters: the identity is resolved in an MCP middleware and used in an
httpx hook several layers away, connected by a ContextVar. Whether that connection
survives the real call stack is not something a unit test can answer.

    pytest tests/test_anonymous_chain.py -v
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastmcp import Client

from pexafy_mcp import anonymous, server, tooling

SECRET = "chain-test-secret"

# One photo, in the envelope the API really returns.
_PAYLOAD = {
    "success": True,
    "data": [
        {
            "photo_id": "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
            "description": "a cup of coffee",
            "photographer": "Someone",
            "urls": {"small": "https://thumb.example/1.jpg"},
        }
    ],
    "meta": {"request_id": "chain-test", "took_ms": 1},
    "pagination": {"has_more": False, "next_cursor": None},
    "error": None,
}


@pytest.fixture
def captured(monkeypatch):
    """Replace the network under the server's own client, keeping every hook."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=_PAYLOAD, headers={"X-Plan": "anonymous", "X-Daily-Quota-Limit": "100"}
        )

    monkeypatch.setattr(
        server.client, "_transport", httpx.MockTransport(handler), raising=False
    )
    return seen


def _decode(token: str) -> dict:
    body = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


async def _call_search(monkeypatch, headers: dict):
    """Run one `search_photos` call with *headers* as the incoming HTTP headers."""
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", SECRET)
    monkeypatch.setattr(anonymous, "_incoming_headers", lambda: headers)
    mcp = server.build_server()
    async with Client(mcp) as client:
        return await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "coffee"})


@pytest.mark.asyncio
async def test_an_anonymous_tool_call_reaches_the_api_with_a_signed_principal(
    monkeypatch, captured
):
    await _call_search(
        monkeypatch,
        {
            "authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
            "cf-connecting-ip": "203.0.113.42",
        },
    )

    assert captured, "no request reached the API"
    request = captured[-1]
    token = request.headers.get(anonymous.PRINCIPAL_HEADER)
    assert token, "the outgoing request carries no principal"

    payload = _decode(token)
    assert payload["src"] == "ip"
    # Keyed by the secret: the address cannot be recovered by trying every IPv4.
    assert payload["s"] == hmac.new(
        SECRET.encode(), b"pexafy-anon-subject:v1:ip:203.0.113.42", hashlib.sha256
    ).hexdigest()[:24]
    assert payload["s"] != hashlib.sha256(b"ip:203.0.113.42").hexdigest()[:24]

    expected = hmac.new(
        SECRET.encode(), token.split(".")[0].encode(), hashlib.sha256
    ).hexdigest()[:32]
    assert token.split(".")[1] == expected, "the API will not verify this signature"


@pytest.mark.asyncio
async def test_the_subject_a_host_sends_is_preferred_over_the_address(monkeypatch, captured):
    monkeypatch.setattr(anonymous, "ATTEST_OPENAI", False)
    await _call_search(
        monkeypatch,
        {
            "authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
            "cf-connecting-ip": "203.0.113.42",
            "x-openai-subject": "sub_person_1",
        },
    )
    payload = _decode(captured[-1].headers[anonymous.PRINCIPAL_HEADER])
    assert payload["src"] == "openai-subject"
    assert payload["s"] == hmac.new(
        SECRET.encode(), b"pexafy-anon-subject:v1:openai-subject:sub_person_1", hashlib.sha256
    ).hexdigest()[:24]


@pytest.mark.asyncio
async def test_two_people_behind_one_host_get_two_principals(monkeypatch, captured):
    """The point of reading the host's id at all: ChatGPT's callers share its addresses."""
    monkeypatch.setattr(anonymous, "ATTEST_OPENAI", False)
    base = {
        "authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
        "cf-connecting-ip": "203.0.113.42",
    }
    await _call_search(monkeypatch, {**base, "x-openai-subject": "person_a"})
    await _call_search(monkeypatch, {**base, "x-openai-subject": "person_b"})

    first, second = (
        _decode(r.headers[anonymous.PRINCIPAL_HEADER])["s"] for r in captured[-2:]
    )
    assert first != second


@pytest.mark.asyncio
async def test_a_caller_from_a_shared_network_sends_no_principal(monkeypatch, captured):
    """Claude.ai. In production it never gets this far — the 401 comes first — but if
    it did, it must not be given someone else's budget."""
    await _call_search(
        monkeypatch,
        {
            "authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
            "cf-connecting-ip": "160.79.104.9",
        },
    )
    assert anonymous.PRINCIPAL_HEADER not in captured[-1].headers


@pytest.mark.asyncio
async def test_a_caller_with_their_own_key_sends_no_principal(monkeypatch, captured):
    """Non-regression: an identified caller keeps their own identity, and only that."""
    await _call_search(
        monkeypatch,
        {"authorization": "Bearer pexafy_api_a_real_key", "cf-connecting-ip": "203.0.113.42"},
    )
    request = captured[-1]
    assert anonymous.PRINCIPAL_HEADER not in request.headers


@pytest.mark.asyncio
async def test_the_identity_does_not_leak_into_the_next_call(monkeypatch, captured):
    """The ContextVar is reset per message; a leak would charge the next caller's
    search to the previous one."""
    monkeypatch.setattr(anonymous, "ATTEST_OPENAI", False)
    await _call_search(
        monkeypatch,
        {
            "authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
            "cf-connecting-ip": "203.0.113.42",
            "x-openai-subject": "person_a",
        },
    )
    assert anonymous.current.get() is None

    await _call_search(
        monkeypatch,
        {"authorization": "Bearer pexafy_api_a_real_key", "cf-connecting-ip": "203.0.113.42"},
    )
    assert anonymous.PRINCIPAL_HEADER not in captured[-1].headers


@pytest.mark.asyncio
async def test_nothing_is_attached_when_the_feature_is_off(monkeypatch, captured):
    monkeypatch.setattr(anonymous, "ENABLED", False)
    monkeypatch.setattr(
        anonymous, "_incoming_headers",
        lambda: {"authorization": f"Bearer {anonymous.SYNTHETIC_BEARER}",
                 "cf-connecting-ip": "203.0.113.42"},
    )
    mcp = server.build_server()
    async with Client(mcp) as client:
        await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "coffee"})
    assert anonymous.PRINCIPAL_HEADER not in captured[-1].headers
