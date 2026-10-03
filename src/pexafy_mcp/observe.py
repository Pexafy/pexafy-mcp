"""What the host says about who is asking — written to the log, never in clear.

Caddy's access log carries the headers ChatGPT adds to a call, and that is what makes
MCP traffic countable on the Pexafy side (`core_mcprequest`). What Caddy does not have
is the request BODY, where the Apps SDK puts `_meta`: this middleware writes one line
per message with the method, the NAMES of the `_meta` keys the host sent, and a short
digest of one value — `openai/subject` — plus, on an `initialize`, the name and version
the client gives itself.

One, because it is the one this server has a use for. OpenAI documents it as the
"anonymized user id sent to MCP servers for the purposes of rate limiting and
identification", and it is what identifies a caller with no account for the daily
allowance (anonymous.py): the line is how one person's calls are told apart when a
quota decision has to be explained. `openai/session` (the conversation) and
`openai/organization` (the workspace) were digested too, to find out whether companies
used the connector. They are no longer: OpenAI's guidelines rule out "surveillance,
tracking, or behavioral profiling — including metadata collection such as timestamps,
IP addresses, or query patterns" beyond what is disclosed and narrowly needed, and a
conversation id or a workspace id is needed for nothing this server does. Their names
still appear among the keys; their values are never read.

The digest is deliberately the same function the Django ingester uses (sha1, first 20
hex), so a line here and a row in `core_mcprequest` for the same person carry the same
string and the two can be joined. It is not a secret-keyed hash: the value it digests is
already OpenAI's pseudonym, random and unguessable, which a digest cannot make easier
to reverse.

It logs and does nothing else: no state, no mutation of the request, no network
call. A failure while looking at metadata must never cost someone a search, so
the body is guarded — and the guard says why it failed rather than passing in
silence.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import tooling

logger = logging.getLogger("pexafy.mcp.meta")

# The keys whose value is digested rather than logged — the one that identifies a
# caller for the allowance, and nothing else (see the module docstring). Every other
# key is reported by NAME only: which fields a host sends, never what they contain.
IDENTIFYING = ("openai/subject",)

# Short labels, so a line stays readable: `subject=…`.
_LABEL = {
    "openai/subject": "subject",
}


def digest(value: str) -> str:
    """Same digest as `core.services.mcp_log._digest` — sha1, first 20 hex.

    Identical on purpose. The Django ingester hashes the two OpenAI headers
    this way; using anything else here (a salt, another algorithm, another
    length) would produce a second, incompatible name for the same person and
    make the two datasets impossible to line up.
    """
    return hashlib.sha1(str(value).encode("utf-8", "replace")).hexdigest()[:20] if value else ""


# ── An e-mail, in a log line ─────────────────────────────────────────────────
# The OAuth path names its user by e-mail (the token's `client_id`). A plain hash of an
# address is no pseudonym: whoever holds the list of accounts hashes every address on it
# and reads the log back — the small-space argument that keys the anonymous caller's
# digest (anonymous.subject_digest). So an e-mail is keyed: HMAC-SHA256 under a secret
# this server already holds, behind a label of its own, so that the value matches no
# other use of that secret.
EMAIL_LOG_DOMAIN = "pexafy-log-email:v1:"

# The key when no secret is configured: this process's own. The lines still tell one
# user from another until the next restart, and the digest is never an unkeyed hash.
_PROCESS_LOG_KEY = secrets.token_bytes(32)

# The secrets that can key it, first found wins: the one shared with Django to resolve
# OAuth tokens — set whenever there is an OAuth user to log — then the anonymous
# principal's.
_LOG_KEY_SECRETS = ("MCP_RESOLVE_SECRET", "PEXAFY_ANON_SECRET")


def _log_key() -> bytes:
    for name in _LOG_KEY_SECRETS:
        secret = os.environ.get(name, "")
        if secret:
            return secret.encode()
    return _PROCESS_LOG_KEY


def email_digest(email) -> str:
    """Who, in a log line, when who is an e-mail: a keyed digest, never the address and
    never a plain hash of it. "?" when there is none."""
    if not email:
        return "?"
    message = f"{EMAIL_LOG_DOMAIN}{email}".encode("utf-8", "replace")
    return hmac.new(_log_key(), message, hashlib.sha256).hexdigest()[:16]


def meta_of(context: Any) -> dict:
    """The `_meta` the CLIENT sent, read where it survives.

    Not from `context.message`: FastMCP rebuilds the params object before running
    middleware — for `tools/call` it constructs a fresh `CallToolRequestParams`
    carrying only its own version meta (fastmcp's `server.py`,
    `_call_tool`/`_read_resource`) — so the host's keys are already gone by the time a
    middleware sees the message. Read there, 67 real tool calls logged `keys=[]`.

    The client's own `_meta` survives one level down, on the SDK's request
    context: `mcp/shared/session.py` sets `request_meta` from
    `validated_request.root.params.meta`, and that is what
    `request_context.meta` returns. That is the reading.

    The message is still consulted as a fallback, because `initialize` is the
    one method FastMCP passes through untouched (`low_level.py` builds its
    context from `responder.request.root`) — and because a direct unit call has
    no request context at all.

    `RequestParams.Meta` allows extra fields, and the host's keys land in
    `model_extra`; `progressToken`, the one field the model declares, is not one
    of ours and is left out by construction.
    """
    meta = None
    fastmcp_context = getattr(context, "fastmcp_context", None)
    if fastmcp_context is not None:
        try:
            request_context = fastmcp_context.request_context
        except Exception:  # noqa: BLE001 — outside a request, the property raises
            request_context = None
        if request_context is not None:
            meta = getattr(request_context, "meta", None)
    if meta is None:
        message = getattr(context, "message", None)
        params = getattr(message, "params", message)
        meta = getattr(params, "meta", None)
    extra = getattr(meta, "model_extra", None)
    return extra if isinstance(extra, dict) else {}


class WithoutArgumentValues(logging.Filter):
    """Keep what a refused call said out of FastMCP's own warning about it.

    FastMCP logs a call whose arguments fail validation as "Invalid arguments for tool
    %r: %s", with pydantic's error list — and every error carries the `input` it
    refused: the sentence somebody searched for, a whole base64 image. The line stays
    (which tool, which field, what was wrong with it); the values go.
    """

    MESSAGE = "Invalid arguments for tool %r: %s"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if record.msg == self.MESSAGE and isinstance(args, tuple) and len(args) == 2:
            name, detail = args
            if isinstance(detail, (list, tuple)):
                detail = [
                    {k: v for k, v in error.items() if k != "input"}
                    if isinstance(error, dict) else "?"
                    for error in detail
                ]
            else:
                # pydantic's own rendering, which quotes each `input_value`: its first
                # line names the tool and the count, and nothing it was sent.
                detail = str(detail).splitlines()[0] if str(detail) else ""
            record.args = (name, detail)
        return True


# The logger FastMCP writes that warning to. A filter on a logger only sees the records
# that logger creates, so it goes on this one, not on a parent.
FASTMCP_CALL_LOGGER = "fastmcp.server.server"


class WithoutQueryString(logging.Filter):
    """Keep the query string out of uvicorn's access line.

    The line is `"<method> <path>?<query> HTTP/1.1" <status>`. No route of this server
    takes a query, but a client can still put one on any URL — a key pasted as
    `/mcp?api_key=…` by a client configured that way — and the line would carry it in
    clear. The path stays, and a `?…` says that a query was there.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn's own call: (client_addr, method, path with query, http_version, status).
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, sep, _query = args[2].partition("?")
            if sep:
                record.args = (args[0], args[1], f"{path}?…", args[3], args[4])
        return True


# The logger uvicorn writes its access line to.
ACCESS_LOGGER = "uvicorn.access"

# What a library writes on its own, below these levels, is what a request said:
#
#   httpx          at INFO, "HTTP Request: GET <url>" for every outgoing request — the
#                  search sentence in the API's query string, the full URL of a
#                  reference image downloaded (the signature of a ChatGPT upload link,
#                  `?se=…&sig=…`, included), the signed URL of a photo handed over;
#   httpcore       at DEBUG, the same exchanges, header by header;
#   mcp            at DEBUG, every JSON-RPC message received, arguments included;
#   sse_starlette  at DEBUG, every chunk of every answer sent;
#   fastmcp        at DEBUG, the arguments of each call it turns into an API request.
#
# So they are held at those floors at least, whatever PEXAFY_MCP_LOG_LEVEL (or
# FASTMCP_LOG_LEVEL) says: DEBUG turns on this server's own lines, never a library's,
# and a quieter level quiets the libraries too. What the API client did is written by
# this server instead, without the query (server._log_api_call).
LIBRARY_LOG_FLOORS = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "mcp": logging.INFO,
    "sse_starlette": logging.INFO,
    "fastmcp": logging.INFO,
}


def _add_once(logger_name: str, kind: type[logging.Filter]) -> None:
    target = logging.getLogger(logger_name)
    if not any(isinstance(f, kind) for f in target.filters):
        target.addFilter(kind())


def install_log_filters() -> None:
    """What keeps a request out of the log, installed once at startup. Idempotent.

    WithoutArgumentValues where FastMCP logs refused calls, WithoutQueryString on
    uvicorn's access line, and the libraries of LIBRARY_LOG_FLOORS held at their
    floor. A floor is set on the library's own logger, as the higher of the floor and
    the level in force (the root's, after `basicConfig`), so a quieter configuration
    stays quieter, and a root set lower later does not bring the library back down.
    """
    _add_once(FASTMCP_CALL_LOGGER, WithoutArgumentValues)
    _add_once(ACCESS_LOGGER, WithoutQueryString)
    for name, floor in LIBRARY_LOG_FLOORS.items():
        library = logging.getLogger(name)
        library.setLevel(max(floor, library.getEffectiveLevel()))


def declares_ui(context: Any) -> bool:
    """Whether an `initialize` declares the MCP Apps extension, in `extensions` or in
    `experimental` (its capabilities, as the request carries them)."""
    message = getattr(context, "message", None)
    caps = getattr(getattr(message, "params", None), "capabilities", None)
    if caps is None:
        return False
    key = "io.modelcontextprotocol/ui"
    extensions = (getattr(caps, "model_extra", None) or {}).get("extensions") or {}
    return key in extensions or key in (getattr(caps, "experimental", None) or {})


def client_of(context: Any) -> str:
    """`clientInfo` on an initialize request, as "name/version", or "".

    The user agent already names the client in the access log, but only the
    body says which version of it — and an `initialize` is the one message that
    carries it.
    """
    message = getattr(context, "message", None)
    info = getattr(getattr(message, "params", None), "clientInfo", None)
    if info is None:
        return ""
    name = getattr(info, "name", "") or ""
    version = getattr(info, "version", "") or ""
    return f"{name}/{version}" if version else name


# Where a client that opens no session names itself. Protocol revision 2026-07-28 drops
# `initialize`, the one message that carried `clientInfo`: the name travels in the
# `_meta` of every request instead, under this key.
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"


def client_in_meta(extra: dict) -> str:
    """The `clientInfo` a request carries in its `_meta` (META_CLIENT_INFO), as
    "name/version", or ""."""
    info = extra.get(META_CLIENT_INFO) if isinstance(extra, dict) else None
    if not isinstance(info, dict) or not info.get("name"):
        return ""
    name, version = str(info["name"]), str(info.get("version") or "")
    return f"{name}/{version}" if version else name


# A client chooses its own name, and the name reaches a log line: one line of it,
# and no longer than a name needs to be.
_CLIENT_NAME_MAX = 80


class LogRequestMeta(Middleware):
    """Log the `_meta` keys of every message, and digest the identifying ones.

    The line of an `initialize` also names the client, `_meta` or not — `clientInfo`,
    as "name/version" — and so does the line of a request that carries its client's
    name in its `_meta`. The name is what tells a host apart where that decides what it
    is served (hosts.py), a directory's scan included, whose client name is documented
    nowhere. It says nothing about the person: no address, no session, no value.
    """

    async def on_message(self, context: MiddlewareContext, call_next):
        try:
            extra = meta_of(context)
            parts = [
                f"{_LABEL[k]}={digest(extra[k])}"
                for k in IDENTIFYING
                if extra.get(k)
            ]
            client = tooling.clean_free_text(
                client_of(context) or client_in_meta(extra), _CLIENT_NAME_MAX)
            if client:
                parts.append(f"client={client}")
            if context.method == "initialize":
                # Whether the client says it shows MCP Apps — the grid (hosts.renders_grid).
                parts.append(f"apps={'yes' if declares_ui(context) else 'no'}")
            # Written for a message with no `_meta` too, as `keys=[]`: an absence is a
            # measurement — the answer to "does this host send anything at all" — and
            # a line that is never written cannot be counted.
            logger.info(
                "meta method=%s keys=%s%s",
                context.method or "?",
                sorted(extra),
                (" " + " ".join(parts)) if parts else "",
            )

        except Exception as exc:  # noqa: BLE001 — a probe must not cost a search
            logger.warning("meta probe failed on %s: %s", context.method or "?", exc)
        return await call_next(context)
