"""The discovery-probe shortcut must be invisible to clients, and narrow.

Production tells this story: clients speaking MCP 2026-07-28 open with a
sessionless `server/discover` POST; the SDK builds a transport for it before
reading the body, then rejects the request; nothing ever collects the transport.
The shortcut in sessions.py answers first. These tests pin the two things that
make it safe to ship:

  - the reply is the one the SDK already sends (same status, same bytes), so a
    client falls back to the legacy handshake exactly as it does today;
  - the same method name *inside an established session* — what Claude-User
    actually sends, 9 times in a 21-hour window — never reaches the shortcut.

The last one is the reason this file exists. Everything else is a detail.
"""
from __future__ import annotations

import importlib
import os

import pytest
from starlette.testclient import TestClient

HTTP_ENV = {"PEXAFY_MCP_TRANSPORT": "http"}

PROBE_BODY = {"jsonrpc": "2.0", "id": "probe", "method": "server/discover", "params": {}}
PROBE_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Mcp-Protocol-Version": "2026-07-28",
    "Mcp-Method": "server/discover",
}
INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


@pytest.fixture
def http_app(monkeypatch):
    """The server as it runs remotely, minus OAuth (the shortcut sits behind it)."""
    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    for key, value in HTTP_ENV.items():
        monkeypatch.setenv(key, value)

    from pexafy_mcp import server

    importlib.reload(server)
    return server.build_server().http_app()


def _post(app, body, headers):
    with TestClient(app) as client:
        return client.post("/mcp", json=body, headers=headers)


def test_probe_gets_the_same_answer_the_sdk_gives(http_app):
    """Byte-for-byte what production answers today — clients cannot tell."""
    response = _post(http_app, PROBE_BODY, PROBE_HEADERS)
    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {"code": -32600, "message": "Bad Request: Missing session ID"},
    }


def test_probe_allocates_no_session(http_app):
    """The whole point: no transport, so nothing to leak.

    The SDK hands out the id of the transport it just orphaned in an
    `Mcp-Session-Id` response header. No header means no transport.
    """
    from pexafy_mcp import sessions

    before = sessions.probes_short_circuited()
    response = _post(http_app, PROBE_BODY, PROBE_HEADERS)
    assert "mcp-session-id" not in {k.lower() for k in response.headers}
    assert sessions.probes_short_circuited() == before + 1


def test_probe_inside_a_live_session_is_left_to_the_sdk(http_app):
    """Claude-User's real behaviour: discover *within* a session it is using.

    That request carries `Mcp-Session-Id`, so the shortcut must not fire — the
    SDK answers it, and the session survives to serve the next tool call.
    """
    from pexafy_mcp import sessions

    with TestClient(http_app) as client:
        opened = client.post("/mcp", json=INITIALIZE_BODY, headers=PROBE_HEADERS | {
            "Mcp-Method": "initialize",
        })
        session_id = opened.headers["mcp-session-id"]

        before = sessions.probes_short_circuited()
        answered = client.post(
            "/mcp", json=PROBE_BODY, headers=PROBE_HEADERS | {"Mcp-Session-Id": session_id}
        )

    assert sessions.probes_short_circuited() == before, "the shortcut swallowed a live session"
    # Whatever the SDK decides to answer, it answers it — and it still knows the
    # session, which is what the next tool call depends on.
    assert answered.headers.get("mcp-session-id") == session_id


def test_a_normal_handshake_still_works(http_app):
    """The body stream is never read by the shortcut, so `initialize` is untouched."""
    response = _post(http_app, INITIALIZE_BODY, PROBE_HEADERS | {"Mcp-Method": "initialize"})
    assert response.status_code == 200
    assert response.headers.get("mcp-session-id")


def test_metrics_reports_the_open_session_count(http_app):
    with TestClient(http_app) as client:
        before = client.get("/metrics").text
        assert "pexafy_mcp_sessions_open" in before

        client.post("/mcp", json=INITIALIZE_BODY, headers=PROBE_HEADERS | {
            "Mcp-Method": "initialize",
        })
        after = client.get("/metrics").text

    def gauge(text):
        line = next(row for row in text.splitlines() if row.startswith("pexafy_mcp_sessions_open "))
        return int(line.split()[1])

    assert gauge(after) == gauge(before) + 1, "a real handshake must move the gauge"


def test_install_is_idempotent(http_app):
    """build_server() runs many times in a test session; the wrapper must not stack."""
    from pexafy_mcp import sessions

    patched = sessions.StreamableHTTPSessionManager.handle_request
    sessions.install()
    assert sessions.StreamableHTTPSessionManager.handle_request is patched


def test_metrics_requires_the_token_when_one_is_configured(monkeypatch):
    """Prometheus reads this endpoint; nobody else should have to be trusted not to."""
    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PEXAFY_MCP_TRANSPORT", "http")
    monkeypatch.setenv("PEXAFY_METRICS_TOKEN", "s3cret")

    from pexafy_mcp import server

    importlib.reload(server)
    app = server.build_server().http_app()

    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        allowed = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        assert allowed.status_code == 200
        assert "pexafy_mcp_sessions_open" in allowed.text


def test_metrics_token_can_come_from_a_file(monkeypatch, tmp_path):
    """Prod points this at the same file Prometheus reads, so the secret lives once."""
    token_file = tmp_path / "monitoring_token"
    token_file.write_text("from-a-file\n")

    for key in [k for k in os.environ if k.startswith("PEXAFY_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PEXAFY_MCP_TRANSPORT", "http")
    monkeypatch.setenv("PEXAFY_METRICS_TOKEN_FILE", str(token_file))

    from pexafy_mcp import server

    importlib.reload(server)
    app = server.build_server().http_app()

    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 401
        assert client.get(
            "/metrics", headers={"Authorization": "Bearer from-a-file"}
        ).status_code == 200
