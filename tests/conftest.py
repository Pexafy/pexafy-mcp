"""Shared test setup.

Every test here is OFFLINE, and this file is what makes that true rather than hoped:

* the configuration the modules read at import is FORCED to harmless test values before
  any of them is imported. CI exports a `.env` copied from `.env.example` — which points
  at the production API — before pytest starts, and a developer's shell may hold a real
  key; neither reaches the code under test;
* `load_dotenv` is disabled, so neither the first import nor a test's reload re-reads a
  `.env` file;
* a guard refuses every real network connection and DNS lookup, and fails the test that
  attempted one — even when the code under test caught the refusal.

The Pexafy API is replaced in memory by the `fake_api` fixture.
"""
from __future__ import annotations

import errno
import importlib
import ipaddress
import os
import socket
import threading

import dotenv
import httpx
import pytest

# ── Configuration ─────────────────────────────────────────────────────────────

# What every module sees at import. The API host cannot resolve (RFC 6761): a request
# that slips past `fake_api` fails here instead of reaching a real server.
TEST_ENV = {
    "PEXAFY_API_BASE_URL": "http://pexafy-api.invalid",
    "PEXAFY_MCP_TRANSPORT": "stdio",
    # Thumbnail proxy off: tests that need the grid reload `previews` with values.
    "PEXAFY_THUMB_BASE_URL": "",
    "PEXAFY_THUMB_HMAC_SECRET": "",
    "PEXAFY_ANON_OPENAI_FEED": "https://openai-feed.invalid/chatgpt-connectors.json",
    "FASTMCP_CHECK_FOR_UPDATES": "off",
}
# Read by the server or by httpx, and never wanted from the outside world here.
_UNSET = (
    "MCP_RESOLVE_SECRET", "OPENAI_APPS_CHALLENGE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
)

for _key in [k for k in os.environ if k.startswith("PEXAFY_")] + list(_UNSET):
    os.environ.pop(_key, None)
os.environ.update(TEST_ENV)

# server.py calls load_dotenv() at import and on every reload; found from the package
# upwards, a local `.env` would reconfigure the modules under test behind their back.
dotenv.load_dotenv = lambda *args, **kwargs: False


@pytest.fixture
def reload_with_env():
    """Reload a module under extra environment variables, and put it back afterwards.

    The modules read their configuration at import, so a test that needs another one
    reloads them. On teardown the environment is restored first, then the module's
    globals, so the next test finds the module exactly as the suite set it up.
    """
    patch = pytest.MonkeyPatch()
    saved: list[tuple[object, dict]] = []

    def reload(module, env: dict[str, str] | None = None):
        for key, value in (env or {}).items():
            patch.setenv(key, value)
        saved.append((module, dict(vars(module))))
        return importlib.reload(module)

    yield reload
    patch.undo()
    for module, namespace in reversed(saved):
        for key in set(vars(module)) - set(namespace):
            delattr(module, key)
        vars(module).update(namespace)


# ── The Pexafy API, in memory ─────────────────────────────────────────────────

class FakeApi:
    """Records every request the server sends and answers it with `respond`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.respond = lambda request: httpx.Response(200, json={"success": True, "data": []})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the network under the server's own HTTP client, keeping every hook."""
    from pexafy_mcp import server

    api = FakeApi()
    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(api))
    return api


# ── Network guard ─────────────────────────────────────────────────────────────

class NetworkBlocked(ConnectionRefusedError):
    """Raised in place of a real connection."""


_attempts: list[str] = []
_INET = (socket.AF_INET, socket.AF_INET6)
_local = threading.local()  # set while socket.socketpair() builds its pair
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto
_real_socketpair = socket.socketpair
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex


def _outbound(sock) -> bool:
    return sock.family in _INET and not getattr(_local, "pairing", False)


def _connect(self, address):
    if _outbound(self):
        _attempts.append(f"connect {address!r}")
        raise NetworkBlocked(f"network access blocked in tests: connect {address!r}")
    return _real_connect(self, address)


def _connect_ex(self, address):
    if _outbound(self):
        _attempts.append(f"connect {address!r}")
        return errno.ECONNREFUSED
    return _real_connect_ex(self, address)


def _sendto(self, *args):
    if _outbound(self):
        _attempts.append(f"sendto {args[-1]!r}")
        raise NetworkBlocked(f"network access blocked in tests: sendto {args[-1]!r}")
    return _real_sendto(self, *args)


def _socketpair(*args, **kwargs):
    # asyncio's self-pipe. AF_UNIX on Linux; where that is missing, the pair is built
    # over loopback, which is not a connection to anything.
    _local.pairing = True
    try:
        return _real_socketpair(*args, **kwargs)
    finally:
        _local.pairing = False


def _resolver(real):
    def resolve(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None:
            try:
                ipaddress.ip_address(name)  # a literal: nothing to look up
            except ValueError:
                _attempts.append(f"DNS {name!r}")
                raise socket.gaierror(
                    socket.EAI_NONAME, f"network access blocked in tests: DNS {name!r}") from None
        return real(host, *args, **kwargs)
    return resolve


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.socket.sendto = _sendto
socket.socketpair = _socketpair
socket.getaddrinfo = _resolver(_real_getaddrinfo)
socket.gethostbyname = _resolver(_real_gethostbyname)
socket.gethostbyname_ex = _resolver(_real_gethostbyname_ex)


@pytest.fixture(autouse=True)
def no_network(request):
    """Fail the test that reached for the network, even if the refusal was swallowed.

    Yields the attempts recorded so far, for the test that checks the guard itself.
    """
    _attempts.clear()
    yield _attempts
    if _attempts:
        seen, _attempts[:] = list(_attempts), []
        pytest.fail(f"{request.node.nodeid} tried to reach the network: {seen}", pytrace=False)
