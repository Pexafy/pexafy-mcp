"""Streamable-HTTP session housekeeping the SDK does not do on its own.

Two things live here, both consequences of one fact: MCP clients have started
speaking protocol revision **2026-07-28**, while the SDK this server runs on
(`mcp` 1.x) tops out at 2025-11-25.

1. **The discovery probe.** A 2026-07-28 client opens with a sessionless
   `server/discover` POST, asking what the server can do before any handshake.
   The SDK does not know the method, and it decides *first* — on the sole fact
   that the request carries no `Mcp-Session-Id` — to build a transport and
   register it in `_server_instances`, only *then* reading the body, finding no
   `initialize`, and answering `400 Bad Request: Missing session ID`. The client
   falls back to the legacy handshake and everything works, but the transport it
   created is unreachable (no client holds its id) and nothing removes it.

   `install()` answers the probe *before* the SDK can allocate anything. The
   reply is the same status and the same JSON-RPC error body, byte for byte, so
   client behaviour is unchanged. The one header dropped is `Mcp-Session-Id`,
   which the SDK sets to the id of the transport it just orphaned.

   The guard is narrow: it fires only on a POST carrying
   `Mcp-Method: server/discover` **and no `Mcp-Session-Id`**, and it never reads
   the request body, so the body stream reaches the SDK untouched. That matters
   because `server/discover` also arrives *inside* an established session, where
   it must keep reaching the SDK — such a request carries a session header, so it
   never reaches this shortcut.

   A probe that arrives with no `Mcp-Method` header at all is left to the SDK:
   catching it would mean parsing the body here to prove it is not an
   `initialize`, reading the stream the SDK must read next.

2. **The gauge.** `open_sessions()` reports how many transports the manager
   holds, so `/metrics` can publish it. The sessions themselves are left alone:
   the SDK can expire idle ones, but real clients go quiet for hours and come
   back, so a timeout short enough to reclaim memory would also cut live users.
"""

from __future__ import annotations

import logging

from mcp.server.streamable_http import CONTENT_TYPE_JSON
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import INVALID_REQUEST, ErrorData, JSONRPCError
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

logger = logging.getLogger("pexafy.mcp.sessions")

# ASGI lowercases header names and keeps them as bytes.
_SESSION_HEADER = b"mcp-session-id"
_METHOD_HEADER = b"mcp-method"
_PROBE_METHOD = b"server/discover"

# Built from the SDK's own models rather than a hardcoded string, so the reply
# cannot drift from the one `_create_error_response` produces for this case.
_PROBE_BODY = JSONRPCError(
    jsonrpc="2.0",
    id="server-error",
    error=ErrorData(code=INVALID_REQUEST, message="Bad Request: Missing session ID"),
).model_dump_json(by_alias=True, exclude_none=True)

# Module state. The manager is captured on the first request it handles: it is
# created inside FastMCP's lifespan, which exposes no injection point, and before
# that first request the honest session count is zero anyway.
_manager: StreamableHTTPSessionManager | None = None
_probes_short_circuited = 0


def _is_discovery_probe(scope: Scope) -> bool:
    """A sessionless `server/discover` POST — see the module docstring."""
    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    mcp_method: bytes | None = None
    for name, value in scope.get("headers", ()):
        if name == _SESSION_HEADER:
            return False  # an established session: the SDK's business, not ours
        if name == _METHOD_HEADER:
            mcp_method = value
    return mcp_method is not None and mcp_method.strip().lower() == _PROBE_METHOD


async def _answer_probe(scope: Scope, receive: Receive, send: Send) -> None:
    response = Response(
        _PROBE_BODY,
        status_code=400,
        headers={"Content-Type": CONTENT_TYPE_JSON},
    )
    await response(scope, receive, send)


def install() -> None:
    """Wrap the session manager so discovery probes never allocate a transport.

    Idempotent — the test suite builds the server many times over. Patching the
    manager rather than adding Starlette middleware puts the shortcut *inside*
    the auth wall: FastMCP wraps the `/mcp` route in `RequireAuthMiddleware`, so
    an unauthenticated probe still gets its 401 exactly as it does today, and
    only a probe that would have reached the SDK is answered here.
    """
    current = StreamableHTTPSessionManager.handle_request
    if getattr(current, "_pexafy_probe_shortcut", False):
        return

    async def handle_request(
        self: StreamableHTTPSessionManager, scope: Scope, receive: Receive, send: Send
    ) -> None:
        global _manager, _probes_short_circuited
        _manager = self
        if _is_discovery_probe(scope):
            _probes_short_circuited += 1
            await _answer_probe(scope, receive, send)
            return
        await current(self, scope, receive, send)

    handle_request._pexafy_probe_shortcut = True  # type: ignore[attr-defined]
    StreamableHTTPSessionManager.handle_request = handle_request  # type: ignore[method-assign]
    logger.info("Discovery-probe shortcut installed (sessionless server/discover)")


def open_sessions() -> int | None:
    """How many transports the session manager is holding, or None if unreadable.

    `_server_instances` is private to the SDK, but FastMCP's own lifespan reaches
    for the same attribute to drain it on shutdown. If a future release renames
    it this returns None and says so in the log rather than reporting a
    plausible-looking zero, which a dashboard cannot tell from a healthy one.
    """
    if _manager is None:
        return 0  # no request handled yet, so nothing is held
    instances = getattr(_manager, "_server_instances", None)
    if instances is None:
        logger.warning(
            "Cannot read the live session count: StreamableHTTPSessionManager has no "
            "_server_instances attribute (SDK version %s?)",
            _sdk_version(),
        )
        return None
    return len(instances)


def probes_short_circuited() -> int:
    """Discovery probes answered without allocating a transport, since startup."""
    return _probes_short_circuited


def _sdk_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mcp")
    except PackageNotFoundError:
        return "unknown"
