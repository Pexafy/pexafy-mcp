"""Say something useful in the 401, instead of only half of it.

The MCP spec's 401 is a header: `WWW-Authenticate` names the protected-resource
metadata, and a client that speaks OAuth follows it. That covers every client
that speaks OAuth. It covers nobody else, and production has both.

20 August 2026, one visitor, four residential IPs, two hours:

    21:38 → 23:49   151 requests to /mcp, 50 of them carrying a credential
                    66 matching token rejections logged on the Django side
                    not one `.well-known` document ever fetched
                    never came back

He was not lost in the OAuth chain — he never entered it. His client does not
speak OAuth. This server also accepts a plain Pexafy API key as the bearer (see
auth.py), which was exactly what he needed, and neither of the two answers he
received mentioned it:

  - with no credential, the body is **empty** — 1741 such responses over two
    months, the most common answer this server gives;
  - with a credential the SDK refuses, the body is 301 bytes of OAuth advice
    ("clear the stored tokens and reconnect, your client will re-register"),
    which is a dead end for a client that has no tokens to clear.

So: keep the status, keep the header, and make both bodies say the two ways in.
The wording differs because the situations do — "you sent nothing" and "what you
sent was refused" are not the same problem, and a person debugging at 23:49
deserves to know which one they have.

Nothing else is touched. A 401 that is neither empty nor one of the SDK's own
JSON refusals passes through byte for byte, and so does every other status.
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

KEYS_URL = "https://pexafy.com/dashboard/api-keys"

# The error codes the auth layer answers 401 with. Anything else in a 401 body
# was written by someone who meant it, and is left alone.
_REWRITABLE_ERRORS = {"invalid_token", "invalid_request", "unauthorized", "invalid_client"}

_NO_CREDENTIAL = {
    "error": "unauthorized",
    "error_description": (
        "This MCP server needs to know who you are. Two ways in: connect with "
        "OAuth — an MCP client does this for you by following the "
        "WWW-Authenticate header on this response — or send a Pexafy API key "
        f"yourself as 'Authorization: Bearer pexafy_api_...'. Create a key at {KEYS_URL}."
    ),
    "keys_url": KEYS_URL,
}

_REFUSED_DESCRIPTION = (
    "The credential you sent was not accepted. If you are using a Pexafy API "
    "key, send the whole key — it starts with 'pexafy_' — as 'Authorization: "
    f"Bearer pexafy_api_...'; create or copy one at {KEYS_URL}. If you are using "
    "OAuth, the access token has expired or was revoked: reconnect the connector "
    "to get a new one."
)


def _presented_a_credential(scope: Scope) -> bool:
    return any(name == b"authorization" for name, _ in scope.get("headers", ()))


def _rewrite(scope: Scope, body: bytes) -> bytes | None:
    """The body to send instead, or None to leave the response as it is."""
    if not body:
        return json.dumps(_NO_CREDENTIAL).encode()
    try:
        original = json.loads(body)
    except (ValueError, TypeError):
        return None  # not ours to rewrite
    if not isinstance(original, dict) or original.get("error") not in _REWRITABLE_ERRORS:
        return None
    # Keep the machine-readable code the SDK chose; replace only the prose, and
    # add the key path it never mentions.
    return json.dumps(
        {
            "error": original["error"],
            "error_description": _REFUSED_DESCRIPTION
            if _presented_a_credential(scope)
            else _NO_CREDENTIAL["error_description"],
            "keys_url": KEYS_URL,
        }
    ).encode()


class UnauthorizedHint:
    """ASGI middleware that gives a 401 a body worth reading.

    A 401 body is tiny (empty, or ~300 bytes), so buffering it to decide is
    free. The response start is held back until the body is known, because the
    substitution changes Content-Length.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        buffered = bytearray()

        async def send_with_hint(message: Message) -> None:
            nonlocal start

            if message["type"] == "http.response.start":
                if message["status"] == 401:
                    start = message  # held until the body is known
                    return
                await send(message)
                return

            if message["type"] == "http.response.body" and start is not None:
                buffered.extend(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = bytes(buffered)
                replacement = _rewrite(scope, body)
                if replacement is None:
                    # Not one of ours: forward what the app wrote, unchanged —
                    # its own headers included (/metrics answers in plain text).
                    await send(start)
                    await send({"type": "http.response.body", "body": body, "more_body": False})
                    return
                body = replacement
                headers = [
                    (name, value)
                    for name, value in start.get("headers", [])
                    if name.lower() not in (b"content-length", b"content-type")
                ]
                headers += [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ]
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return

            await send(message)

        await self.app(scope, receive, send_with_hint)
