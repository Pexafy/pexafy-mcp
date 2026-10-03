"""The OpenAI IP list: loaded from the first call, and asked for sparingly when it fails.

`time.monotonic()` counts from the host's boot on Linux, so a list "fetched at 0.0" looked
fresh for the first hour after a reboot and was never loaded: every ChatGPT caller then
fell back to the shared address identity. And a failing feed was fetched again on every
message, inside the request, by every concurrent caller.

The clock is simulated (only the module's view of it) and the feed is an
httpx.MockTransport: nothing here depends on the machine's uptime or the network.
"""
from __future__ import annotations

import asyncio
import time
import types

import httpx
import pytest

from pexafy_mcp import anonymous

OPENAI_IP = "23.102.140.115"
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 120.0}  # a host booted two minutes ago
    monkeypatch.setattr(anonymous, "time", types.SimpleNamespace(
        monotonic=lambda: now["t"], time=time.time))
    return now


@pytest.fixture
def feed(monkeypatch):
    f = anonymous._IPFeed("https://feed.invalid/chatgpt-connectors.json", ttl=3600)
    monkeypatch.setattr(anonymous, "openai_feed", f)
    monkeypatch.setattr(anonymous, "ATTEST_OPENAI", True)
    return f


def _serve(monkeypatch, handler):
    calls = {"n": 0}

    async def counting(request):
        calls["n"] += 1
        response = handler(request)
        if asyncio.iscoroutine(response):
            response = await response
        return response

    class _Client(_RealAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(counting)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(anonymous.httpx, "AsyncClient", _Client)
    return calls


def _listing(request):
    return httpx.Response(200, json={"prefixes": [{"ipv4Prefix": "23.102.140.112/28"}]})


def _down(request):
    return httpx.Response(403, text="challenge")


async def test_a_list_never_loaded_is_fetched_even_right_after_boot(monkeypatch, clock, feed):
    calls = _serve(monkeypatch, _listing)
    assert await anonymous.attest_openai(OPENAI_IP) is True
    assert calls["n"] == 1


async def test_a_loaded_list_is_kept_for_its_ttl_then_refreshed(monkeypatch, clock, feed):
    calls = _serve(monkeypatch, _listing)
    await anonymous.attest_openai(OPENAI_IP)
    clock["t"] += 3599
    await anonymous.attest_openai(OPENAI_IP)
    assert calls["n"] == 1
    clock["t"] += 2
    await anonymous.attest_openai(OPENAI_IP)
    assert calls["n"] == 2


async def test_a_failing_feed_is_asked_again_only_after_a_pause(monkeypatch, clock, feed):
    clock["t"] = 10_000.0  # a host up for hours: the boot-time case is not what is tested
    calls = _serve(monkeypatch, _down)
    for _ in range(5):
        assert await anonymous.attest_openai(OPENAI_IP) is None
    assert calls["n"] == 1
    clock["t"] += anonymous.FEED_RETRY - 1
    await anonymous.attest_openai(OPENAI_IP)
    assert calls["n"] == 1
    clock["t"] += 2
    await anonymous.attest_openai(OPENAI_IP)
    assert calls["n"] == 2


async def test_a_failed_refresh_keeps_the_good_list_in_use(monkeypatch, clock, feed):
    clock["t"] = 10_000.0
    _serve(monkeypatch, _listing)
    assert await anonymous.attest_openai(OPENAI_IP) is True
    calls = _serve(monkeypatch, _down)
    clock["t"] += 3601
    for _ in range(3):
        assert await anonymous.attest_openai(OPENAI_IP) is True
    assert calls["n"] == 1


async def test_concurrent_callers_share_one_fetch(monkeypatch, clock, feed):
    async def slow(request):
        await asyncio.sleep(0.05)
        return _listing(request)

    clock["t"] = 10_000.0
    calls = _serve(monkeypatch, slow)
    await asyncio.gather(*(anonymous.attest_openai(OPENAI_IP) for _ in range(5)))
    assert calls["n"] == 1
    assert feed.loaded
