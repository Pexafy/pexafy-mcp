"""Give the 401 a body worth reading.

The MCP spec answers an unauthenticated request with a header: `WWW-Authenticate`
names the protected-resource metadata, and a client that speaks OAuth follows it.
That covers every client that speaks OAuth, and nobody else.

This server also accepts a plain Pexafy API key as the bearer (see auth.py), and
neither answer a caller receives mentions it: with no credential the body is empty,
and with a credential the SDK refuses the body is OAuth advice — "clear the stored
tokens and reconnect" — which is a dead end for a client that holds no tokens.

So: keep the status, keep the header, and make both bodies name the two ways in. The
wording differs because the situations do — "you sent nothing" and "what you sent was
refused" are not the same problem.

Nothing else is touched. A 401 that is neither empty nor one of the SDK's own JSON
refusals passes through byte for byte, and so does every other status.
"""
from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import limits

# The page that lists a caller's API keys: the one the key-limit message names, set by
# the same variable (PEXAFY_CONNECTORS_URL). Its default keeps the trailing slash;
# without it Django answers with a redirect.
KEYS_URL = limits.CONNECTORS_URL

# The form that makes a key, which every 401 links, in its prose and in `keys_url`:
# the list above is one click short of it. "MCP Agent" is the client origin to pick there.
CREATE_KEY_URL = KEYS_URL.rstrip("/") + "/create/"

# The error codes the auth layer answers 401 with. Anything else in a 401 body
# was written by someone who meant it, and is left alone.
_REWRITABLE_ERRORS = {"invalid_token", "invalid_request", "unauthorized", "invalid_client"}

_NO_CREDENTIAL = {
    "error": "unauthorized",
    "error_description": (
        "This MCP server needs to know who you are. Two ways in: connect with "
        "OAuth — an MCP client does this for you by following the "
        "WWW-Authenticate header on this response — or send a Pexafy API key "
        "yourself as 'Authorization: Bearer pexafy_api_...'. Create an MCP Agent key "
        f"at {CREATE_KEY_URL}."
    ),
    "keys_url": CREATE_KEY_URL,
}

_REFUSED_DESCRIPTION = (
    "The credential you sent was not accepted. If you are using a Pexafy API "
    "key, send the whole key — it starts with 'pexafy_' — as 'Authorization: "
    f"Bearer pexafy_api_...'; create an MCP Agent key at {CREATE_KEY_URL}. If you "
    "are using OAuth, the access token has expired or was revoked: reconnect the "
    "connector to get a new one."
)


# The two 401s anonymous.py answers on purpose (anonymous.SIGN_IN_SCOPE_KEY). The host
# shows its own sign-in prompt; these words reach the model only when the person
# declines it — measured in VS Code, which then hands the body over as the tool's
# answer. They say what to do next, and nothing that sends the model looking for a key.
_SIGN_IN_BODIES = {
    # Measured with Copilot's small model (2026-10-01): "asked to sign in again" read as
    # an instruction to sign in, and it called connect_account — another prompt. The
    # action comes first, and connect_account is named only to be ruled out.
    "start": (
        "The person declined to sign in. Call the same tool again now, with the same "
        "arguments: it works without an account. Do not call connect_account unless the "
        "person asks to sign in."
    ),
    "connect": (
        "The person declined to sign in. Carry on: searching works without an account. "
        "Do not call connect_account again unless the person asks to sign in."
    ),
}


def _presented_a_credential(scope: Scope) -> bool:
    return any(name == b"authorization" for name, _ in scope.get("headers", ()))


def _rewrite(scope: Scope, body: bytes) -> bytes | None:
    """The body to send instead, or None to leave the response as it is."""
    sign_in = _SIGN_IN_BODIES.get(scope.get("pexafy.sign_in") or "")
    if sign_in:
        return json.dumps({"error": "unauthorized", "error_description": sign_in}).encode()
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
            "keys_url": CREATE_KEY_URL,
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
