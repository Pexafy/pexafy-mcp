"""The monitoring bearer must never be sent to Django as an OAuth token.

Prometheus scrapes /metrics with it every 15 s. From 2026-08-26 the auth
middleware resolved it against /oauth/mcp/resolve on every scrape: 5 760 × 401 a
day on the Django side and the same "Token resolution rejected" line a real
failure produces, 240 times an hour, in this server's log.
"""
from __future__ import annotations

import httpx
import pytest

from pexafy_mcp.auth import PexafyResolveVerifier

METRICS = "mon-token-0123456789abcdef"


def _verifier(calls: list[str], not_oauth=(METRICS,)):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization", ""))
        return httpx.Response(401, json={"error": "invalid_token"})

    return PexafyResolveVerifier(
        "http://django/oauth/mcp/resolve", "resolve-secret",
        not_oauth_tokens=list(not_oauth),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.anyio
async def test_the_metrics_token_is_answered_locally_without_a_log(caplog):
    calls: list[str] = []
    verifier = _verifier(calls)
    with caplog.at_level("INFO", logger="pexafy_mcp.auth"):
        assert await verifier.verify_token(METRICS) is None
    assert calls == [], "the metrics token reached Django's resolve endpoint"
    assert not [r for r in caplog.records if "rejected" in r.message]


@pytest.mark.anyio
async def test_any_other_bearer_is_still_resolved_and_a_rejection_still_logged(caplog):
    calls: list[str] = []
    verifier = _verifier(calls)
    with caplog.at_level("INFO", logger="pexafy_mcp.auth"):
        assert await verifier.verify_token("some-opaque-oauth-token") is None
    assert calls == ["Bearer some-opaque-oauth-token"]
    assert [r for r in caplog.records if "rejected" in r.message]


@pytest.mark.anyio
async def test_an_empty_metrics_token_never_matches():
    """No token configured must not turn "" into a universal skip."""
    calls: list[str] = []
    verifier = _verifier(calls, not_oauth=("",))
    assert await verifier.verify_token("") is None
    assert calls == ["Bearer "]
