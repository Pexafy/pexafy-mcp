"""Pexafy MCP server: an MCP client of the Pexafy image-search API.

The text search is generated from the Pexafy public OpenAPI spec (the vendored
snapshot `assets/openapi.json`, or PEXAFY_OPENAPI_URL), so the API stays the source
of truth for its parameters and response schema; tooling.py tunes the spec for an
LLM first. This module adds the rest, and `build_server()` assembles it:

  - four hand-written tools: search by image (a `photo_id`, an image URL, an upload
    or base64 bytes), the photograph as a file, the grid's selection (selection.py)
    and account linking (linking.py);
  - hooks on the API client: nothing on a request but what the API needs (outbound.py),
    the credential a call carries (auth.py, anonymous.py), the page size, plan limits
    and failures turned into readable answers (limits.py, budget.py), and each result
    enriched for the inline grid (previews.py);
  - the MCP middleware chain (with compat.py, guards.py, origin.py, observe.py), the
    inline-grid resource (widget.py), the `find_photos` prompt, and the HTTP routes:
    /health, /metrics, the server card, /selection and /widget (sessions.py and
    unauthorized.py adjust the HTTP layer).

Entry point: `pexafy-mcp` (console script) → `main()`. Transport `stdio` (Claude
Desktop/Code) or `http` (remote Streamable HTTP), set by PEXAFY_MCP_TRANSPORT.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

# Must run before fastmcp and the local modules are imported: they read configuration
# at import time. With no path, find_dotenv starts from this module's directory, not
# the working directory (except in a REPL or under a debugger), and walks up: a source
# checkout finds the repository's .env. In Docker the variables come from the
# environment.
load_dotenv()

# FastMCP checks PyPI for updates on startup. `fastmcp.settings` is built at import,
# so this must be set before fastmcp is imported. setdefault preserves an explicit
# opt-in through FASTMCP_CHECK_FOR_UPDATES.
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")

import httpx  # noqa: E402 — must follow the env setup above
from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.server.dependencies import get_access_token, get_http_headers  # noqa: E402
from fastmcp.apps import AppConfig, ResourceCSP, UI_MIME_TYPE  # noqa: E402
from fastmcp.server.providers.openapi import MCPType, OpenAPITool, RouteMap  # noqa: E402
from mcp.types import (  # noqa: E402
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    TextContent,
    ToolAnnotations,
)
from fastmcp.server.middleware import (  # noqa: E402
    Middleware as ToolMiddleware,
    MiddlewareContext,
)
from fastmcp.server.transforms import ToolTransform  # noqa: E402
from fastmcp.tools.function_tool import FunctionTool  # noqa: E402
from fastmcp.tools.tool import ToolResult  # noqa: E402
from fastmcp.tools.tool_transform import (  # noqa: E402
    ArgTransformConfig,
    ToolTransformConfig,
)
from starlette.middleware import Middleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response  # noqa: E402

from . import __version__  # noqa: E402
from . import anonymous  # noqa: E402
from . import budget  # noqa: E402
from . import coach  # noqa: E402
from . import linking  # noqa: E402
from . import compat  # noqa: E402
from .env import TRUE_WORDS  # noqa: E402
from . import guards  # noqa: E402
from . import hosts  # noqa: E402
from . import observe  # noqa: E402
from . import origin  # noqa: E402
from . import outbound  # noqa: E402
from . import previews  # noqa: E402 — must follow load_dotenv (reads env at import)
from . import selection  # noqa: E402 — same reason
from . import limits  # noqa: E402
from . import sessions  # noqa: E402
from . import tooling  # noqa: E402
from . import unauthorized  # noqa: E402
from . import widget  # noqa: E402
from .auth import (  # noqa: E402
    ANONYMOUS_CLAIM,
    API_KEY_CLAIM,
    QUOTA_MESSAGE_CLAIM,
    PexafyResolveVerifier,
    RootAliasedAuthProvider,
)

# --- Observability ----------------------------------------------------------
# Logs go to stdout. Level from PEXAFY_MCP_LOG_LEVEL.
logging.basicConfig(
    level=os.environ.get("PEXAFY_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("pexafy.mcp")
# No search sentence, image, token, e-mail or address in clear in this server's log: the
# modules below log digests and kinds, and this keeps what the libraries would write on
# their own out of it — httpx's line for every outgoing URL (a search sentence, a signed
# image link), FastMCP's warning quoting a refused call, the query string of uvicorn's
# access line, the DEBUG dumps of whole messages. See observe.install_log_filters.
observe.install_log_filters()

# Static assets shipped with the package: the OpenAPI snapshot and the widget's
# vendored SDK bundle, regenerated by prepare.sh. Importing this module performs no
# network I/O.
_ASSETS = Path(__file__).resolve().parent / "assets"

# --- Configuration ----------------------------------------------------------
# API_BASE_URL is the API *root*; the OpenAPI paths already include "/api/v1".
API_BASE_URL = os.environ.get("PEXAFY_API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("PEXAFY_API_KEY", "")
SOURCE_HEADER = os.environ.get("PEXAFY_SOURCE", "MCP-Agent")  # analytics tag
HTTP_TIMEOUT = float(os.environ.get("PEXAFY_HTTP_TIMEOUT", "30"))

# Spec source: the vendored snapshot (assets/openapi.json) by default — deterministic,
# offline, no import-time network. Set PEXAFY_OPENAPI_URL to fetch a live spec instead
# (opt-in); PEXAFY_OPENAPI_PATH points at a different local file.
OPENAPI_URL = os.environ.get("PEXAFY_OPENAPI_URL", "")
OPENAPI_FILE = os.environ.get("PEXAFY_OPENAPI_PATH", str(_ASSETS / "openapi.json"))

# OAuth Resource Server. Set together on the HTTP transport, they make the server
# require a Bearer OAuth token, resolve it to the user's Pexafy API key through the
# Django Authorization Server, and call the API with that key. See auth.py.
TRANSPORT = os.environ.get("PEXAFY_MCP_TRANSPORT", "stdio")
OAUTH_RESOLVE_URL = os.environ.get("PEXAFY_OAUTH_RESOLVE_URL", "")
OAUTH_RESOLVE_SECRET = os.environ.get("MCP_RESOLVE_SECRET", "")
# Public URLs advertised in OAuth discovery (RFC 9728 / 8414):
#   - MCP_PUBLIC_URL: this server's public base (the protected resource).
#   - OAUTH_AS_URL:   the Django Authorization Server's public issuer; clients
#                     fetch <AS>/.well-known/oauth-authorization-server from it.
MCP_HOST = os.environ.get("PEXAFY_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("PEXAFY_MCP_PORT", "8765"))
MCP_PUBLIC_URL = os.environ.get("PEXAFY_MCP_PUBLIC_URL", f"http://{MCP_HOST}:{MCP_PORT}")
OAUTH_AS_URL = os.environ.get("PEXAFY_OAUTH_AS_URL", "")
OAUTH_ENABLED = bool(
    TRANSPORT == "http" and OAUTH_RESOLVE_URL and OAUTH_RESOLVE_SECRET and OAUTH_AS_URL
)

# Bearer token guarding /metrics. Prometheus reads the same secret from a file
# (`credentials_file` in its scrape config), so both ends point at one token. Left
# empty the endpoint is open, which suits a local run and not a deployment.
METRICS_TOKEN_FILE = os.environ.get("PEXAFY_METRICS_TOKEN_FILE", "")
METRICS_TOKEN = (
    Path(METRICS_TOKEN_FILE).read_text().strip()
    if METRICS_TOKEN_FILE
    else os.environ.get("PEXAFY_METRICS_TOKEN", "").strip()
)

# Starlette middleware the remote server runs. Passed to `http_app()`/`run()`, so it
# wraps the route that issues the 401. See unauthorized.py.
HTTP_MIDDLEWARE = [Middleware(unauthorized.UnauthorizedHint)]

# Human-facing name, shown by directories that read the server card. `mcp.name`
# beside it is the protocol identifier ("pexafy"). tests/test_server_card.py pins this
# to the `title` in server.json, which is what the MCP registry publishes.
SERVER_TITLE = "Pexafy"

# The origin the inline grid is attributed to. Nothing is served from it: ChatGPT
# renders the widget under `<widgetDomain>.web-sandbox.oaiusercontent.com`, so the
# value is an isolation label, one origin per app. It is also where "Open in Pexafy"
# sends the viewer from the fullscreen view, hence the website rather than the MCP
# host.
WIDGET_DOMAIN = os.environ.get("PEXAFY_WIDGET_DOMAIN", "https://pexafy.com")


def _spec_ui_domain() -> str:
    """The origin the MCP Apps spec wants in `_meta.ui.domain` — for Claude.

    Not `openai/widgetDomain`: Claude accepts nothing but `{hash}.claudemcpcontent.com`
    here ("Invalid ui.domain format") and renders no widget at all otherwise. The spec
    leaves the field's format to each host, and an OpenAI host reads the same field as
    its app's origin: every host that is not Claude is served WIDGET_DOMAIN there instead
    (hosts.WidgetDomainPerHost).

    The value is derived, not issued: sha256 of this server's own endpoint URL, first
    32 hex characters, under `claudemcpcontent.com`, so any server computes its own.
    The endpoint string has to be exact: scheme, host, `/mcp`, no trailing slash.

    Empty when PEXAFY_MCP_PUBLIC_URL is set empty, which leaves the field unset rather
    than wrong.
    """
    base = MCP_PUBLIC_URL.rstrip("/")
    if not base:
        return ""
    endpoint = base if base.endswith("/mcp") else f"{base}/mcp"
    return hashlib.sha256(endpoint.encode()).hexdigest()[:32] + ".claudemcpcontent.com"


def _load_openapi_spec() -> dict:
    """Load the spec from the live URL if PEXAFY_OPENAPI_URL is set, else the
    vendored snapshot file. No network unless explicitly opted in."""
    if OPENAPI_URL:
        resp = httpx.get(OPENAPI_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    if Path(OPENAPI_FILE).is_file():
        return json.loads(Path(OPENAPI_FILE).read_text())
    raise RuntimeError(f"OpenAPI spec not found: {OPENAPI_FILE} (run ./prepare.sh)")


# What a request to the API carries: HTTP's own headers and this server's defaults, then
# what the hooks below put on it on purpose — nothing the host sent. FastMCP's generated
# search copies the incoming request's headers onto it (the caller's address, ChatGPT's
# `x-openai-*`, cookies, `origin`…): they go, and the caller's address is replaced by a
# pseudonym, for the one count the API keeps per address. See outbound.py.
async def _only_what_the_api_needs(request: httpx.Request) -> None:
    dropped = outbound.restrict(request, client.headers)
    if dropped:
        # The names only: their values are what must not travel, nor be written here.
        logger.debug("Not passed on to the API: %s", ", ".join(dropped))
    outbound.name_the_caller(request)


# The credential each API request carries, first match wins:
#   0. An anonymous caller (the token's anonymous claim): the env service key, and the
#      signed principal naming the person it acts for.
#   1. OAuth: the user's Pexafy API key, which PexafyResolveVerifier put in the token's
#      claims, or the plan-limit message when no key could be issued.
#   2. A key the client sent itself (`x-api-key` or `Authorization: Bearer <key>`).
#   3. None: the env API_KEY set on the client (stdio, a local run).
async def _forward_client_key(request: httpx.Request) -> None:
    # 0) A caller with no credential of their own. The env service key (already on the
    # client) reaches the API, and the signed principal says which person it is acting
    # for — the API counts that principal, not the key. Returning here also keeps the
    # synthetic bearer out of `x-api-key`, which step 2 below would otherwise do.
    token = get_access_token()
    if token is not None and token.claims and token.claims.get(ANONYMOUS_CLAIM):
        if not anonymous.attach_principal(request):
            # No identity and no principal: the API refuses the service key on its own
            # (PRINCIPAL_REQUIRED), which is the intended outcome — one shared key must
            # not become one shared budget. Said here so the log shows why.
            logger.info("Anonymous call with no resolvable identity — the API will refuse it")
        return

    # 1) OAuth-resolved key from the validated token's claims.
    if token is not None and token.claims:
        # No key could be minted for this user: surface the limit as the tool error,
        # which the host shows in chat.
        blocked = token.claims.get(QUOTA_MESSAGE_CLAIM)
        if blocked:
            # `client_id` is the user's e-mail here: a keyed digest groups the lines
            # without putting the address in the log (observe.email_digest).
            logger.info("Tool call blocked by plan limit for user %s",
                        observe.email_digest(token.client_id))
            raise ToolError(blocked)
        resolved = token.claims.get(API_KEY_CLAIM)
        if resolved:
            request.headers["x-api-key"] = resolved
            # `client_id` is the user's e-mail: a keyed digest, like the plan-limit line
            # above.
            logger.debug("Auth: OAuth-resolved key for %s",
                         observe.email_digest(token.client_id))
            return

    # 2) Direct per-user key forwarded from the incoming request headers.
    incoming = get_http_headers(include={"x-api-key", "authorization"})
    client_key = incoming.get("x-api-key", "")
    if not client_key:
        auth = incoming.get("authorization", "")
        if auth.lower().startswith("bearer "):
            client_key = auth[7:].strip()
    if client_key:
        request.headers["x-api-key"] = client_key
        logger.debug("Auth: direct per-request client key")
        return

    # 3) No credential of any kind: the env API_KEY on the client goes out, with the
    # signed principal if ResolveIdentity found an identity. That identity only exists
    # behind the synthetic bearer, which step 0 takes when the OAuth provider is on and
    # step 2 takes, as a key, when it is off: as the code stands, no principal is
    # attached here.
    if anonymous.attach_principal(request):
        logger.debug("Auth: anonymous principal, service key")
        return

    logger.debug("Auth: no client key — falling back to env API_KEY")


# Page size of every search, by text or by reference. Forced on the request whatever
# the caller passes (`per_page`, `limit` and `cursor` are not on the tools);
# score_threshold is left alone, so the engine's relevance cut-off decides how many of
# these photos come back.
GRID_PAGE_SIZE = 16


async def _grid_page_size(request: httpx.Request) -> None:
    if "/search/photos" not in request.url.path:
        return
    request.url = request.url.copy_set_param("per_page", str(GRID_PAGE_SIZE))


# A spent allowance comes back as a result, not an error. A tool that raises renders no
# widget, and an error result reaches neither the frame (no `structuredContent`) nor,
# behind the host's connect dialog, the model, which then invented results (measured).
# So the 429 becomes an ordinary answer with no photos: the grid draws the wall where
# the results were, and the model reads `notice` (the allowance is spent, and when it
# returns) and says the same in words. What stops a retry loop is that body, not the
# status. A rate limit does not come through here: it clears in seconds, and "wait,
# then try once more" is the right answer. The connect dialog is kept for somebody who
# asks for it (see linking.py).
#
# PEXAFY_BUDGET_WALL_GRID=0 goes back to the error result.
WALL_AS_GRID = os.environ.get("PEXAFY_BUDGET_WALL_GRID", "1").strip().lower() in (
    "1", "true", "yes", "on")


def _wall_block(headers) -> dict | None:
    """The budget block a refusal carries, for a grid that gets mounted anyway."""
    try:
        return budget.wall(headers)
    except Exception:  # never let the screen break the refusal
        logger.exception("Could not compose the wall block")
        return None


def _wall_prompt(headers) -> str:
    """The sentence the HOST shows beside its connect button, written for a person.

    `budget.wall` already composes it — the title and what happens next — and it is the
    same wording the grid would have drawn, so the two surfaces cannot drift apart.
    """
    try:
        block = budget.wall(headers)
        return f"{block['title']}. {block['detail']}"
    except Exception:  # never let the wording break the refusal
        logger.exception("Could not compose the wall prompt")
        return ""


def _wall_as_grid(response: httpx.Response, message: str) -> bool:
    """Rewrite a 429 into an empty 200 the grid can draw. True when it did.

    The headers are left exactly as they came — `budget.wall` reads them for which
    allowance ran out and when it comes back, and `_enrich_response` (a later hook)
    finds the block already in place and leaves it alone.
    """
    if not WALL_AS_GRID:
        return False
    try:
        wall = budget.wall(response.headers)
        body = {
            "success": True,
            "data": [],
            "notice": message,
            # The numbers and the sentence. The wall's own wording, its button and who
            # is asking are the grid's, and go to `_meta` (budget.keep_for_grid).
            "budget": budget.for_model(wall),
        }
        raw = json.dumps(body).encode()
        response.status_code = 200
        response._content = raw  # already fully read above; downstream reads see this
        response.headers["content-length"] = str(len(raw))
        response.headers["content-type"] = "application/json"
        budget.keep_for_grid(account=budget.account(response.headers),
                             budget=budget.for_grid(wall))
    except Exception:  # a wall we cannot build must not eat the refusal
        logger.exception("Could not render the budget wall — falling back to a tool error")
        return False
    logger.info("Budget wall rendered as an empty grid (plan=%s)",
                response.headers.get("X-Plan", "?"))
    return True


async def _surface_plan_limits(response: httpx.Response) -> None:
    """Answer an API plan-limit 429 in words the caller can act on.

    A rate limit becomes a tool error. A spent daily or monthly allowance becomes the
    wall: by default an empty answer the grid draws (_wall_as_grid), otherwise a
    linking.BudgetWall that the tool-call boundary turns into an error result.

    The numbers come from the response headers (X-Plan, X-Quota-Limit,
    X-RateLimit-Limit), so no extra usage call is needed.

    A rate limit and a monthly quota must be told apart: only the first clears with
    time, and telling an assistant to wait on the second sends it into a futile retry
    loop. Both carry the same headers, so the discriminator is `error.code` in the JSON
    body (RATE_LIMITED vs QUOTA_EXCEEDED), with Retry-After as the fallback when a
    proxy has stripped the body.
    """
    if response.status_code != 429:
        return
    plan = response.headers.get("X-Plan", "")
    code = ""
    try:
        body = json.loads(await response.aread())
        if isinstance(body, dict):
            code = (body.get("error") or {}).get("code", "") or ""
    except Exception:  # fall back to the header heuristic
        code = ""
    retry_after = response.headers.get("Retry-After")

    # A day is the third case, and it is not the other two. Its Retry-After is real —
    # the budget does come back — but it is hours away, so the rate-limit message
    # ("wait 40000s, then try once more") would be absurd, and the monthly one would be
    # false. Told apart by the code; the fallback below reads the daily header, which
    # only an anonymous response carries.
    if code == "DAILY_QUOTA_EXCEEDED" or (
        not code and "X-Daily-Quota-Limit" in response.headers and retry_after
        and _is_hours_away(retry_after)
    ):
        logger.info("Plan limit surfaced: daily quota (plan=%s)", plan or "?")
        # Whether naming connect_account leads anywhere: only a host that shows its own
        # account-connection prompt (ChatGPT) turns that call into a connection. Read
        # here, inside the tool call, where the session that names the host is in scope.
        message = await limits.daily_quota_message(
            plan, response.headers.get("X-Daily-Quota-Limit"), retry_after,
            host_can_connect=hosts.is_openai())
        if _wall_as_grid(response, message):
            return
        # Not a ToolError: only the tool-call boundary can build the result a wall
        # needs — its structured content, and the connect challenge on `_meta` when
        # that is enabled. See linking.OfferAccountLink.
        raise linking.BudgetWall(message, prompt=_wall_prompt(response.headers),
                                 signed_in=budget._signed_in(response.headers),
                                 block=_wall_block(response.headers),
                                 account=budget.account(response.headers))

    if code == "QUOTA_EXCEEDED":
        is_rate = False
    elif code == "RATE_LIMITED":
        is_rate = True
    else:
        # No code, so the body was stripped: a Retry-After means a rate limit.
        is_rate = "retry-after" in response.headers
    if is_rate:
        logger.info("Plan limit surfaced: rate-limit (plan=%s)", plan or "?")
        raise ToolError(await limits.rate_limit_message(
            plan, response.headers.get("X-RateLimit-Limit"), retry_after))
    logger.info("Plan limit surfaced: monthly quota (plan=%s)", plan or "?")
    message = await limits.monthly_quota_message(plan, response.headers.get("X-Quota-Limit"))
    if _wall_as_grid(response, message):
        return
    raise linking.BudgetWall(message, prompt=_wall_prompt(response.headers),
                             signed_in=budget._signed_in(response.headers),
                             block=_wall_block(response.headers),
                             account=budget.account(response.headers))


async def _surface_search_errors(response: httpx.Response) -> None:
    """Turn a failed text search into a sentence, like the by-image tool does.

    Otherwise FastMCP hands the model its raw "HTTP error 503: … - {body}". Only the
    text search (GET /search/photos): the other calls on this client handle their own
    failures, and a 429 has already been answered by _surface_plan_limits.
    """
    request = response.request
    if (response.status_code < 400 or response.status_code == 429
            or request.method != "GET" or not request.url.path.endswith("/search/photos")):
        return
    detail = ""
    try:
        body = json.loads(await response.aread())
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        detail = message.strip() if isinstance(message, str) else ""
    except ValueError:  # not JSON: a proxy's error page, say nothing about it
        pass
    logger.warning("Text search failed: HTTP %s", response.status_code)
    raise ToolError(f"Pexafy could not run that search. {detail}".strip())


def _is_hours_away(retry_after) -> bool:
    """A per-minute limit clears in seconds; a daily one clears at midnight."""
    try:
        return int(retry_after) > 300
    except (TypeError, ValueError):
        return False


# On a successful JSON body, read once, mutated, written back: the paging block and the
# API's telemetry go (tooling.prune_result); each photo is pruned, ranked and given its
# signed preview URLs; and, when the headers carry it, the answer gets the allowance
# (`budget`, the model's half of it). Who is asking and the panel's wording are handed
# to the grid on `_meta` (budget.keep_for_grid). The preview URLs feed the inline grid
# (widget.py, previews.py) and never carry the HMAC secret.
#
# Everything is removed HERE, before FastMCP builds the result: the text block a model
# reads is serialised from this body once, when the result is built, and a field taken
# out of `structuredContent` afterwards would still be in that text.
async def _enrich_response(response: httpx.Response) -> None:
    if response.status_code >= 400:
        return
    if "json" not in response.headers.get("content-type", ""):
        return
    try:
        data = json.loads(await response.aread())
    except Exception:  # leave non-JSON / unparseable bodies untouched
        return
    # No `cursor` in, so no next_cursor out; and no request id or timing, which serve
    # the API's own logs and nobody who reads the answer. See tooling.REMOVE_RESULT_FIELDS.
    tooling.prune_result(data)
    # Take the dead weight off each photograph before anything else reads it — see
    # tooling.REMOVE_PHOTO_FIELDS for what goes and why. What the 0.4.12 grid reads of it
    # is set aside first, for the answers that grid may still draw from ChatGPT's cache
    # (compat.KeepLegacyGrid); outside that window, nothing is.
    for photo in (data.get("data") or []) if isinstance(data, dict) else []:
        compat.set_aside_for_legacy_grid(photo)
        tooling.prune_photo(photo)
    # `rank` numbers the results of this answer, so that a client without the grid can
    # name one ("the second one"). It is data, never drawn: the grid paints no number on
    # a result tile, and the #n it does paint numbers the reader's selection.
    previews.inject_ranks(data)
    previews.inject_preview_urls(data)
    # What is left of today's allowance, when it is worth saying. Absent from almost
    # every response — see budget.read — so its presence is the signal. The model gets
    # the numbers and the sentence; the panel's wording and its link go to the grid.
    notice = budget.read(response.headers)
    if notice and isinstance(data, dict) and "budget" not in data:
        # `not in`: a wall built by _wall_as_grid is already there and says more than
        # the plain counter would — which allowance ran out, and when it returns.
        data["budget"] = budget.for_model(notice)
        budget.keep_for_grid(budget=budget.for_grid(notice))
    # Who is asking. Unlike the notice this rides on every answer: the head's account
    # button is drawn on every answer, and a reader who wants to sign in should not
    # have to spend 80% of an allowance to find out where. The grid's alone: it goes to
    # `_meta` (budget.ACCOUNT_META_KEY), not into the body the model reads.
    budget.keep_for_grid(account=budget.account(response.headers))
    new = json.dumps(data).encode()
    response._content = new  # already fully read & cached; downstream reads see the new body
    response.headers["content-length"] = str(len(new))


async def _log_api_call(response: httpx.Response) -> None:
    """One line per API call — method, path, status — in place of httpx's own.

    httpx writes the whole URL, and the query string of a text search is the sentence
    somebody searched for; it is held at WARNING for that (observe.LIBRARY_LOG_FLOORS).
    This line keeps what the count needs and nothing a person wrote: the path of every
    call this client makes is fixed, or carries a catalogue `photo_id`.
    """
    request = response.request
    logger.info("API %s %s %s", request.method, request.url.path, response.status_code)


# HTTP client: env API_KEY is the default/fallback (stdio); the request hook overrides
# it with the calling client's key when present (HTTP). X-Source tags analytics.
# `retries` re-attempts only a connection that could not be established (refused, name
# not resolved, or timed out while connecting): nothing that reached the API is re-sent,
# so neither a 4xx/5xx nor a connection reset mid-request is retried.
HTTP_RETRIES = int(os.environ.get("PEXAFY_HTTP_RETRIES", "2"))
client = httpx.AsyncClient(
    base_url=API_BASE_URL,
    headers={"x-api-key": API_KEY, "X-Source": SOURCE_HEADER},
    timeout=HTTP_TIMEOUT,
    transport=httpx.AsyncHTTPTransport(retries=HTTP_RETRIES),
    event_hooks={
        # First, so that _forward_client_key completes a request already cut down to what
        # the API needs: nothing the host sent rides along — a principal it forged among
        # it — but what that hook puts there on purpose, the caller's own key.
        "request": [_only_what_the_api_needs, _forward_client_key, _grid_page_size],
        # _log_api_call writes the call's line first, whatever the hooks after it do;
        # _surface_plan_limits answers a 429 (a tool error, or the wall as an empty
        # answer); _surface_search_errors raises on any other failed text search;
        # _enrich_response reworks a successful body.
        "response": [_log_api_call, _surface_plan_limits, _surface_search_errors,
                     _enrich_response],
    },
)

# ── Tool & resource definitions (assembled into a server by build_server) ─────

# The MCP `instructions` field: read once, up front, for the whole server, unlike a
# tool description, which is weighed only when that tool is considered. Claude Code
# loads only the tool NAMES and this field at session start (the tools themselves are
# searched for on demand), so on that host this is all the model knows of the server.
#
# Three blocks, in the order a routing decision is made: the needs that should fire
# the server, what it is not for, then one line per tool naming it exactly. What a
# result contains is said on the tools, not here — OpenAI asks that this field not
# repeat the tool descriptions, and keep what matters most in its first 512 characters.
#
# So the field opens on the trigger, and the sentence on what Pexafy is follows the
# bullets. Placed first, that sentence spent 167 of the 512 characters and left room
# for one whole bullet; after them, three fit whole, with the opening of the fourth
# ("Free, stock, royalty-free…"), and a host that loads the whole field still reads it.
#
# Under 2,048 characters: Claude Code truncates this field (and every tool description)
# there, and the tail is the tool routing. A test pins the limit.
#
# It is about THIS server only: it names no other tool and states no preference
# against one — OpenAI's submission rules forbid steering the model between tools.
#
# The triggers are what the USER asks for or says they need, never a need the model
# infers on its own: OpenAI refuses "overly broad triggering beyond the explicit user
# intent", and Anthropic a description that leads the model to call a tool the user
# did not request. The library names are provenance ("its own index"), not a promise
# to relay a query elsewhere, and no line compares this server with image generation.
# "Images" is not in the list of requests: a request for an image is not always one for
# a photograph, as "When NOT" says, and Anthropic reads a trigger that broad as a
# contradiction (policy 2.A, "narrow, unambiguous"); "show me images of…" stays among
# the phrasings quoted on search_photos, where the request itself is the example.
#
# "In any language" sits in the opening sentence, inside the first 512 characters,
# rather than in a last bullet that fell outside them. A picture the host can neither
# link nor pass to a tool is still in scope, in words: the by-image search cannot
# receive it, the text search can find its scene. And "Do not use Pexafy to add…" is
# about this server: an unscoped "do not add photographs" reads as a rule over every
# tool (Anthropic 2.E).
_INSTRUCTIONS_HEAD = (
    "Use this server when the user asks for real photographs, or states a need that photographs fill, in any language, for example:\n"
    "\n"
    "* Finding, showing or providing photos or pictures.\n"
    "* Photographs for articles, blogs, websites, landing pages, products, slides, social posts, ads, newsletters, banners, thumbnails, covers, posters, backgrounds, wallpapers or mood boards.\n"
    "* Content the user asks to illustrate with photographs (e.g. \"write an article with pictures\").\n"
    "* Free, stock, royalty-free, commercially usable or documentary photographs.\n"
    "* A real photograph, when the user asks for one.\n"
    "* Free alternatives to a picture, or photos that look like it — one the user links to or that the host passes as a file, or a Pexafy photo already found.\n"
    "\n"
    "Pexafy searches its own index of millions of free-to-use photographs from stock libraries such as Unsplash, Pexels and Pixabay, each with its licence and credit line.\n"
    "\n"
    "### When NOT to use Pexafy\n"
    "\n"
    "A request for a visual is not always a request for a photograph. Pexafy does not find illustrations, drawings, logos, icons, diagrams or UI screenshots, does not generate, edit or upscale images, and does not search for named people; finding free photographs like a picture the user provides is in scope: by image when it is linked or passed to the tool, in words otherwise. Do not use Pexafy to add photographs the user did not ask for.\n"
    "\n"
    "### Tool selection\n"
    "\n"
)


def server_instructions(*, file_tool: bool = True, selection_tool: bool = True,
                        grid: bool = True) -> str:
    """The instructions, naming only the tools this server registers, and the grid only
    where there is one.

    The file tool exists only with previews configured, the selection tool only with the
    grid it reads (previews configured) and PEXAFY_SELECTION_TOOL on; a line that sends
    the model to a tool that is not there reads perfectly well and fails, and a photo
    "liked in the Pexafy grid" cannot be liked where no grid is drawn. Names are
    interpolated from the registry.
    """
    names = tooling.PUBLIC_TOOL_NAMES
    liked = " or liked in the Pexafy grid" if grid else ""
    lines = [
        f"* To find photographs described in words, call `{names['search_photos']}`.",
        f"* To find photographs that look like a reference, call `{names['search_photos_by_image']}` with exactly one reference: `image_url`, `image_file` or `image_base64` for a picture, or `photo_id` for a Pexafy photo returned in an earlier search{liked}.",
    ]
    if selection_tool:
        lines.append(f"* To read the photographs the user liked in the Pexafy grid, call `{names['get_selected_photos']}`.")
    if file_tool:
        # "edit" names the host's own tools: Pexafy edits nothing (see "When NOT"), it
        # hands over the file that a crop or a caption is then made on.
        lines.append(f"* To get a photograph found here as an image file — to insert, attach or download it, or to edit it with the host's own tools — call `{names['get_photo_file']}`.")
    return _INSTRUCTIONS_HEAD + "\n".join(lines)


# The full text, as production serves it (every optional tool on).
SERVER_INSTRUCTIONS = server_instructions()

# Display titles shown by the client (the snake_case names stay the call ids).
TOOL_TITLES = {
    # A title is read by the model as well as shown to the user, in the host's permission
    # prompt among others: short, and a name rather than a pitch. "Search photos by
    # description" quietly says a description is required — which steers a plain keyword
    # query somewhere else, and a keyword query is exactly what this tool answers well.
    tooling.PUBLIC_TOOL_NAMES["search_photos"]: "Search free stock photos",
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: "Find photos that look like an image or a Pexafy photo",
}

# Its own constant, not a TOOL_TITLES entry: that map is checked against the OpenAPI
# operationIds, and this tool has none — it is written here, like connect_account.
PHOTO_FILE_TITLE = "Get the photo as a file"

# The order `tools/list` presents, most central first. Not cosmetic: a router reads the
# list from the top and does not always weigh every description, so the first tool sets
# what the server appears to be for. Left alone, FastMCP lists the tools written here
# (LocalProvider) ahead of the one generated from the spec (OpenAPIProvider), which puts
# the text search, the tool for the common request, last.
#
# Sorted by OrderTools rather than by the order of `add_tool` calls: the generated tool
# is not added by one, and the provider order is FastMCP's to change. A tool missing
# from this tuple keeps working and sorts after the listed ones, in its original order.
TOOL_ORDER = (
    tooling.PUBLIC_TOOL_NAMES["search_photos"],
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
    tooling.PUBLIC_TOOL_NAMES["get_photo_file"],
    tooling.PUBLIC_TOOL_NAMES["get_selected_photos"],
    tooling.PUBLIC_TOOL_NAMES["connect_account"],
)


class OrderTools(ToolMiddleware):
    """Present the tools in TOOL_ORDER, most central first.

    A middleware rather than a sort at assembly time: the generated tool and the
    hand-written ones arrive from two different FastMCP providers, and the order they
    are concatenated in belongs to the framework, not to us. Hooking the listing is the
    one place that sees all of them together, whoever built them.

    Unknown names keep their relative order at the end, so a tool added later is listed
    rather than lost — the failure mode of a sort key that raises on a missing entry.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        rank = {name: i for i, name in enumerate(TOOL_ORDER)}
        return sorted(tools, key=lambda t: rank.get(t.name, len(rank)))


async def _listed(mcp: FastMCP, kind: str) -> list:
    """The `tools`, `resources` or `prompts` as `<kind>/list` sends them, for a probe.

    A probe (/health, the server card) is not an MCP message, so it does not go through
    the whole middleware chain, which would log it as a host's listing (observe.py) and
    look for an anonymous caller. Only the chain's `on_list_<kind>` hooks run, in the
    chain's order: they are what shapes a listing (OrderTools, and the `$ref` inlining
    FastMCP installs by default).
    """
    async def listed(_context):
        return await getattr(mcp, f"list_{kind}")(run_middleware=False)

    call_next = listed
    for middleware in reversed(mcp.middleware):
        call_next = partial(getattr(middleware, f"on_list_{kind}"), call_next=call_next)
    return list(await call_next(MiddlewareContext(message={}, method=f"{kind}/list")))


# The mark of mcp.pexafy.com/favicon.svg (Caddy, production), byte for byte: a "P" in
# the site's violet-to-cyan on a dark tile.
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
    '<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">'
    '<stop offset="0%" stop-color="#8b5cf6"/><stop offset="100%" stop-color="#06b6d4"/>'
    '</linearGradient></defs><rect width="64" height="64" rx="14" fill="#0a0a0f"/>'
    '<text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" '
    'font-family="Inter, system-ui, sans-serif" font-weight="700" font-size="38" '
    'fill="url(#g)">P</text></svg>'
)

# The tools whose answer is not a page of photographs, and which therefore must not
# carry the inline grid.
NO_GRID_TOOLS = frozenset(
    tooling.PUBLIC_TOOL_NAMES[n] for n in ("connect_account", "get_photo_file", "get_selected_photos")
)

# The tools the GRID itself calls, and which therefore have to be reachable from inside
# the iframe. ChatGPT refuses a component's `window.openai.callTool` unless the tool
# declares `openai/widgetAccessible` (Apps SDK reference), and refuses it silently. The
# widget calls exactly three: search_photos_by_image with a `photo_id` (the ≈),
# connect_account (the account panel, which polls check_only), and search_photos (the
# head's shape filter re-runs the search the grid came from). Nothing else belongs
# here: this is a door into the server that does not pass through the model, so it is
# opened tool by tool and only for the ones the widget presses.
WIDGET_TOOLS = frozenset(
    tooling.PUBLIC_TOOL_NAMES[n] for n in ("search_photos_by_image", "search_photos", "connect_account")
)

# The GENERATED tools whose API arguments wear their public names — `q` and
# `orientation` become tooling.PUBLIC_QUERY_PARAM and tooling.PUBLIC_ORIENTATION_PARAM,
# through an ArgTransform in build_server. Only the text search: the hand-written
# by-image tool takes the public names in its own signature, so an ArgTransform on it
# would rename arguments that do not exist.
RENAMED_ARGUMENT_TOOLS = frozenset({tooling.PUBLIC_TOOL_NAMES["search_photos"]})

# What ChatGPT shows next to the tool call while it runs and once it returns —
# `_meta["openai/toolInvocation/invoking"]` / `invoked`, 64 characters at most each
# (Apps SDK reference). User-facing, so plain words; neutral on completion, because
# "photos found" would be a lie on an empty grid. A test pins the length.
TOOL_STATUS = {
    tooling.PUBLIC_TOOL_NAMES["get_selected_photos"]: ("Reading your Pexafy selection…", "Read your selection"),
    tooling.PUBLIC_TOOL_NAMES["connect_account"]: ("Opening Pexafy account connection…", "Ready to connect"),
    tooling.PUBLIC_TOOL_NAMES["get_photo_file"]: ("Fetching the photo from Pexafy…", "Photo ready"),
    tooling.PUBLIC_TOOL_NAMES["search_photos"]: ("Searching Pexafy for photos…", "Searched Pexafy"),
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: ("Searching Pexafy by image…", "Searched Pexafy by image"),
}

# Tool names are derived from the API's operationIds; an entry here sets the public name
# instead. Keyed on the operationId, so regenerating the vendored spec cannot silently
# undo it (build_server refuses a key that no longer exists).
TOOL_NAME_OVERRIDES: dict[str, str] = {
    # The text search takes its name from the registry.
    "search_photos_api_v1_search_photos_get": tooling.PUBLIC_TOOL_NAMES["search_photos"],
}


def _grid_html() -> str:
    """Body of the inline result-grid MCP App resource (see widget.py)."""
    return widget.GRID_HTML


def _grid_csp_domains() -> tuple[list[str], list[str]]:
    """What the grid may load, and what it may reach: (resource, connect) domains.

    One answer for the two places the grid is served — the MCP resource's CSP and the
    /widget route's header — so that the page a browser shows and the one a host shows
    cannot be allowed different things.

    Resources: the thumbnail CDN its pictures come from (plus the SDK's CDN when the
    bundle is not inlined). Connections: what it reaches with `fetch`, which is this
    server's own origin, where it posts the reader's selection (/selection — without it
    that channel fails silently, see selection.py), and nothing else. The thumbnail CDN
    is an `img-src` only: the grid draws its pictures and never reads their bytes. It
    was declared a connection too while the grid handed photographs to the host as
    files (uploadFile, gone), and "the plugin review process checks the declared policy
    against the UI behavior" (OpenAI): an allowance nothing uses is one to justify for
    nothing.
    """
    resource = [d for d in (previews.THUMB_ORIGIN, *widget.RESOURCE_EXTRA_DOMAINS) if d]
    connect = list(widget.RESOURCE_EXTRA_DOMAINS)
    public = MCP_PUBLIC_URL.rstrip("/")
    if public and public not in connect:
        connect.append(public)
    return resource, connect


# Search by image. The spec's POST operation (excluded in build_server) takes a
# multipart file, which a chat assistant cannot supply. This tool takes its reference as
# a `photo_id`, an image URL, a host upload or base64 bytes, fetches or decodes the image
# server-side, and posts to the same endpoint through `client`, so the auth, page-size
# and enrichment hooks all apply.
#
# The description says what the tool takes and when. The reasons live in the comments
# of search_photos_by_image, not in the text: a model weighs explanation written for a
# human reader as one more instruction.
_IMG_FETCH_TIMEOUT = float(os.environ.get("PEXAFY_IMG_FETCH_TIMEOUT", "15"))
# What the API takes as a reference image: JPEG, PNG, WebP or AVIF only, told apart by
# their first bytes (`_validate_image_bytes` in src/apis/routers/search_routes.py), in a
# request of 10 MiB at most (`MAX_BODY_SIZE` in src/apis/config.py). That cap is
# MaxBodySizeMiddleware's, on the WHOLE multipart body: the image and the framing of its
# part (boundary, field name, file name, type), about 170 bytes plus the name. So the
# image is held a margin below it, and the name sent is cut short (_UPLOAD_NAME_MAX) so
# the framing stays far inside the margin. Held to that here, an image the API would
# refuse is refused before it is downloaded in full or sent, with a sentence that says
# what to send — not after the round trip, in the API's words.
_API_BODY_MAX_BYTES = 10 * 1024 * 1024
_MULTIPART_MARGIN = 16 * 1024
_API_IMAGE_MAX_BYTES = _API_BODY_MAX_BYTES - _MULTIPART_MARGIN
_MAX_IMG_BYTES = int(os.environ.get("PEXAFY_IMG_MAX_BYTES", str(_API_IMAGE_MAX_BYTES)))
# The file name the upload carries. The last segment of a URL can run to thousands of
# characters, and it is the one part of the framing the caller writes; the API reads the
# bytes and never the name.
_UPLOAD_NAME_MAX = 100
# The image URL comes from the caller, so every address it resolves to must be public.
# PEXAFY_IMG_ALLOW_PRIVATE=1 lifts that for a local run serving test images from
# localhost; never set it on a deployed server.
_IMG_ALLOW_PRIVATE = os.environ.get("PEXAFY_IMG_ALLOW_PRIVATE", "").strip().lower() in (
    "1", "true", "yes", "on")
_IMG_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Deliberately vague: a status, a content type or a host in the answer would make this
# tool a probe of whatever the server can reach.
_IMG_BAD_URL = "Please provide a direct http(s) URL to an image (JPEG, PNG, WebP or AVIF)."
_IMG_UNREACHABLE = "Could not download that image URL."
_IMG_NOT_AN_IMAGE = "That URL did not return an image."
# For an image sent as bytes, the same list of formats as `_IMG_BAD_URL` gives.
_IMG_B64_FORMAT = "`image_base64` is not a JPEG, PNG, WebP or AVIF image."


def _size_label(n: int) -> str:
    """A byte count the way a refusal says it: "10 MB", "7.5 MB", "900 KB"."""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}".removesuffix(".0") + " MB"
    return f"{max(1, round(n / 1024))} KB"


def _img_too_large() -> str:
    """The refusal of an image over the cap. Worded from `_MAX_IMG_BYTES` itself, so the
    figure said and the figure enforced cannot differ."""
    return f"That image is too large (max {_size_label(_MAX_IMG_BYTES)})."


# The triggers are the user's wish for photographs LIKE a picture, not any request
# about a picture ("describe this image" is not one). And the attachment is described
# as it works: `image_file` is filled only by a host that passes uploads to tools, so
# the text promises no search from an image that merely sits in the conversation — and
# says where such a picture does go: its scene, in words, to the text search. The grid
# is named only where there is one (previews configured).
def image_tool_description(*, grid: bool = True) -> str:
    """The by-image tool's description, with the grid or without it."""
    liked = " or liked in the grid" if grid else ""
    shown = (" In a host that renders it, the grid shows them; the answer omits image links."
             if grid else "")
    return (
        f"Use this when the user wants photographs that look like a picture, rather than ones described in words: a picture they link to, one the host passes to the tool as a file, or a Pexafy photo already found{liked}.\n"
        "\n"
        "Call it for “find images like this”, “something similar to this photo”, “find a free alternative to this image”, “I need this but royalty-free”, “photos in this style”, “more like the second one”, whether or not they say “search by image”.\n"
        "\n"
        f"Give exactly one reference: `photo_id` for a Pexafy photo from an earlier result{liked}; `image_url` for a picture at a public URL, such as a link the user pasted; `image_file` when the host passes the user's uploaded image to the tool; `image_base64` for image bytes a client already holds. Sending two is refused. "
        f"A picture that is only visible in the conversation — not linked, not passed to the tool — is not a reference this tool can receive; its scene can be searched in words with `{tooling.PUBLIC_TOOL_NAMES['search_photos']}`. "
        "Words are optional, in `english_search_sentence`: with a `photo_id`, the words that photograph was found under; with an image, only what the user wants changed or kept (“like this but at night”).\n"
        "\n"
        f"It finds free stock photographs in Pexafy's index that visually match the reference. The reference image is sent to Pexafy only to run the search; it is not stored. Each result carries a `rank`, a `photo_id`, its licence, the credit line to display and an image link.{shown}"
    )
# No shared block is appended to the descriptions: what a result carries in full is
# declared once, in the output schema `tools/list` sends.


# The schema `image_file` must carry, fixed by OpenAI's Apps SDK. A host that uploads a
# file into a tool call fills these fields in, and a declaration that differs is
# rejected at review: all four properties declared even though two are optional,
# exactly `download_url` and `file_id` required, no other field.
#
# Written out rather than inferred. `dict | None` yields
# `{"anyOf": [{"type": "object", "additionalProperties": true}, {"type": "null"}]}`,
# which declares no properties at all, and a Pydantic model sits behind the same
# `anyOf` wrapper. The parameter stays optional by being absent from the tool's
# `required` list: an optional property, not a nullable one.
FILE_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    # A sibling of `type`, not a fifth property: their contract constrains what the
    # file object contains, and every other parameter of every other tool carries
    # one. Kept in the literal, so the test that pins this schema pins it too.
    # What the object is and who has one, not who writes it. "Not by the model" might
    # keep ChatGPT's model from naming the upload in its call, should that be how the
    # file reaches the tool there — never measured, so the text takes no side.
    "description": (
        "The user's uploaded image, as a host that passes uploads to tools provides it: the upload's `download_url` and `file_id`. A picture that is only visible in the conversation has neither."
    ),
    "properties": {
        "download_url": {"type": "string"},
        "file_id": {"type": "string"},
        "mime_type": {"type": "string"},
        "file_name": {"type": "string"},
    },
    "required": ["download_url", "file_id"],
    "additionalProperties": False,
}


def _is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """False for private, loopback, link-local, multicast, reserved or unspecified
    addresses, an IPv4 address written as IPv6 included."""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.version == 6 and ip.is_site_local:
        return False
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


async def _resolve_host(host: str, port: int) -> list[str]:
    """Every address *host* resolves to; a literal address is its own answer."""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def _checked_address(url: httpx.URL) -> str:
    """The address to connect to for *url*, refused unless every address is public."""
    host = url.raw_host.decode("ascii")
    port = url.port or (443 if url.scheme == "https" else 80)
    addresses = [ipaddress.ip_address(a) for a in await _resolve_host(host, port)]
    if not addresses:
        raise ToolError(_IMG_UNREACHABLE)
    if not _IMG_ALLOW_PRIVATE and not all(_is_public_address(a) for a in addresses):
        logger.info("Refused an image URL whose host is not public: %s", host)
        raise ToolError(_IMG_UNREACHABLE)
    # IPv4 first: image hosts nearly always have one, and not every server routes IPv6.
    chosen = min(addresses, key=lambda a: a.version)
    if chosen.version == 6 and chosen.ipv4_mapped is not None:
        chosen = chosen.ipv4_mapped
    return str(chosen)


# Declared types that do not rule an image out: bytes stored with no type of their own
# (S3's `binary/octet-stream` default, Drive and CDNs serving uploads as
# `application/octet-stream`), or no type at all. The first bytes decide, as they do for
# a type that says `image/…`.
_UNTYPED_CONTENT = frozenset({"", "application/octet-stream", "binary/octet-stream"})


async def _read_image(response: httpx.Response) -> tuple[bytes, str]:
    if not response.is_success:
        raise ToolError(_IMG_UNREACHABLE)
    ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if not (ctype.startswith("image/") or ctype in _UNTYPED_CONTENT):
        raise ToolError(_IMG_NOT_AN_IMAGE)
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_IMG_BYTES:
        raise ToolError(_img_too_large())
    data = bytearray()
    sniffed = False
    async for chunk in response.aiter_bytes():
        data += chunk
        if len(data) > _MAX_IMG_BYTES:
            raise ToolError(_img_too_large())
        # A GIF, an SVG, a HEIC is an image the API refuses: known from the first bytes,
        # before the rest is downloaded.
        if not sniffed and len(data) >= _IMAGE_SNIFF_BYTES:
            if _image_type(bytes(data[:_IMAGE_SNIFF_BYTES])) is None:
                raise ToolError(_IMG_BAD_URL)
            sniffed = True
    kind = _image_type(bytes(data))
    if kind is None:
        raise ToolError(_IMG_BAD_URL)
    # The format the bytes are, not the one the header claims ("image/jpg"…).
    return bytes(data), kind


async def _download_image(url: httpx.URL) -> tuple[bytes, str]:
    """GET *url*, following redirects by hand so that every hop is checked."""
    # No proxy from the environment: the connection has to reach the checked address.
    async with httpx.AsyncClient(timeout=_IMG_FETCH_TIMEOUT, follow_redirects=False,
                                 trust_env=False) as c:
        for _ in range(_IMG_MAX_REDIRECTS + 1):
            if url.scheme not in ("http", "https") or not url.host:
                raise ToolError(_IMG_UNREACHABLE)
            # Connect to the address just checked, so a second DNS answer cannot move
            # the request (rebinding); the name still goes in Host and in the TLS SNI.
            request = c.build_request(
                "GET", url.copy_with(host=await _checked_address(url)),
                headers={"Host": url.netloc.decode("ascii"), "User-Agent": "PexafyMCP/1.0"},
                extensions={"sni_hostname": url.raw_host.decode("ascii")},
            )
            response = await c.send(request, stream=True)
            try:
                location = response.headers.get("location")
                if response.status_code in _REDIRECT_STATUSES and location:
                    url = url.join(location)
                    continue
                return await _read_image(response)
            finally:
                await response.aclose()
    raise ToolError(_IMG_UNREACHABLE)


async def _fetch_image(url: str) -> tuple[bytes, str, str]:
    raw = url.strip()
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raise ToolError(_IMG_BAD_URL)
    try:
        target = httpx.URL(raw)
    except httpx.InvalidURL as exc:
        raise ToolError(_IMG_BAD_URL) from exc
    try:
        # One deadline for the whole download: httpx's timeout restarts at every read,
        # so a body sent a few bytes at a time would never trip it.
        async with asyncio.timeout(_IMG_FETCH_TIMEOUT):
            data, ctype = await _download_image(target)
    except ToolError:
        raise
    except (httpx.HTTPError, httpx.InvalidURL, OSError, TimeoutError, ValueError) as exc:
        logger.info("Image download failed: %s", type(exc).__name__)
        raise ToolError(_IMG_UNREACHABLE) from exc
    name = (raw.rsplit("/", 1)[-1].split("?")[0] or "image").strip() or "image"
    return data, ctype, name


# Enough of a file to tell its format by: AVIF's brand ends at byte 12.
_IMAGE_SNIFF_BYTES = 12


def _image_type(data: bytes) -> str | None:
    """The MIME type of *data* if it is a format the API takes, by its first bytes — the
    API's own test (`_validate_image_bytes`), WebP checked a little more strictly — or
    None for anything else, a GIF included."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


def _decode_base64_image(b64: str) -> tuple[bytes, str, str]:
    raw = b64.strip()
    if raw.startswith("data:"):  # tolerate a data: URI prefix
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("`image_base64` is not valid base64.") from exc
    if not data:
        raise ToolError("`image_base64` decoded to empty data.")
    if len(data) > _MAX_IMG_BYTES:
        raise ToolError(_img_too_large())
    ctype = _image_type(data)
    if ctype is None:
        raise ToolError(_IMG_B64_FORMAT)
    return data, ctype, "upload"


async def search_photos_by_image(
    image_url: str | None = None,
    image_file: dict | None = None,
    image_base64: str | None = None,
    photo_id: str | None = None,
    english_search_sentence: str | None = None,
    explicit_orientation_filter: list[str] | None = None,
) -> ToolResult:
    # ONE reference, given one of four ways:
    #   photo_id     — a photograph of the catalogue, from an earlier search here;
    #   image_base64 — raw bytes a programmatic client already holds (decoded here);
    #   image_file   — auto-injected by ChatGPT for an upload (fetch its download_url);
    #   image_url    — a public URL the user gave (a link pasted into the chat).
    # The API takes exactly one — `image` in the body, or `photo_id` in the query — and
    # refuses both at once rather than guessing which was meant, so two references are
    # refused here too, before anything is fetched.
    ref = (photo_id or "").strip()
    given = [name for name, value in (("photo_id", ref), ("image_url", image_url),
                                       ("image_file", image_file), ("image_base64", image_base64))
             if value]
    if len(given) > 1:
        raise ToolError(
            "Give one reference: " + " and ".join(f"`{n}`" for n in given) + " were both "
            "sent. `photo_id` names a photograph from an earlier search here; `image_url`, "
            "`image_file` and `image_base64` carry a picture from anywhere else."
        )
    words = (english_search_sentence or "").strip()
    # `explicit_orientation_filter` is the name on the MCP surface; `orientation` is the
    # API's name and what goes on the wire. The generated text tool gets the same rename
    # through an ArgTransform in build_server; here it is one line, because this tool
    # builds its own query. httpx renders a list as a repeated parameter, which is how
    # the API takes several shapes: they OR together.
    params: dict[str, object] = {}
    if explicit_orientation_filter is not None:
        params[tooling.API_ORIENTATION_PARAM] = explicit_orientation_filter
    try:
        if ref:
            # The catalogue photo as the reference: its stored vector, blended with the
            # words it was found under so the neighbours stay on the subject. No body —
            # the request is the query string, and httpx writes `Content-Length: 0`; the
            # API refuses a `photo_id` sent with any body at all. `text_alpha` is not
            # sent: the API's own default applies, and this follows it if it moves.
            params["photo_id"] = ref
            if words:
                params["q"] = words
            resp = await client.post("/api/v1/search/photos", params=params)
        else:
            if image_base64:
                data, ctype, name = _decode_base64_image(image_base64)
            else:
                url = ""
                if isinstance(image_file, dict):
                    url = image_file.get("download_url") or image_file.get("url") or ""
                url = url or (image_url or "")
                if not url:
                    raise ToolError(
                        "Provide the reference: `photo_id` for a photograph from an "
                        "earlier search here, or `image_url` (a public URL), `image_file` "
                        "or `image_base64` for a picture from anywhere else. A picture "
                        "that is only visible in the conversation can be searched in "
                        f"words with `{tooling.PUBLIC_TOOL_NAMES['search_photos']}`."
                    )
                data, ctype, name = await _fetch_image(url)
            # Words ride along with the picture ("like this but at night"). `text_alpha`
            # is not sent, so the API applies its own balance between words and image.
            if words:
                params["q"] = words
            resp = await client.post(
                "/api/v1/search/photos",
                files={"image": (name[:_UPLOAD_NAME_MAX], data, ctype)}, params=params,
            )
        resp.raise_for_status()
    except ToolError:
        raise  # our own messages (the reference, the image, a plan limit) as they are
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = ((exc.response.json() or {}).get("error") or {}).get("message", "")
        except Exception:  # not the API's error envelope: no detail to add
            pass
        what = "find photos like that one" if ref else "search by that image"
        raise ToolError(f"Pexafy could not {what}. {detail}".strip()) from exc
    return ToolResult(structured_content=resp.json())


# ── Handing over the photograph itself ───────────────────────────────────────
# A URL is a promise an assistant cannot keep when asked to PUT a photo somewhere — on a
# slide, in a document, under a caption: it has no way to fetch the bytes. MCP carries
# images (`ImageContent`, base64 and a MIME type), and this tool answers with one, for
# ONE photograph, on request: base64 for every result would be a large, useless
# addition to the model's context (see previews.py).
#
# The bytes are the picture already served, at the permanent width: from our thumbnail
# proxy, or from the source's own resizing CDN (see _RESIZING_SOURCES). No new access
# to anybody's images, and no new storage.
PHOTO_FILE_MAX_BYTES = int(os.environ.get("PEXAFY_PHOTO_FILE_MAX_BYTES", str(6 * 1024 * 1024)))

# Whether the bytes go out a second time, as an embedded resource (see get_photo_file):
#   auto   (default)  for an OpenAI host only — the one the second copy was added for;
#                     Claude caps a tool result (about 150,000 characters on claude.ai,
#                     25,000 tokens in Claude Code), and two base64 copies of one
#                     photograph are what reach the cap (hosts.py);
#   1/true/yes/on     for every host;
#   anything else     for none.
PHOTO_FILE_RESOURCE = os.environ.get("PEXAFY_PHOTO_FILE_RESOURCE", "").strip().lower() or "auto"


def _photo_file_as_resource() -> bool:
    """Whether this caller gets the file's bytes a second time, as a resource."""
    if PHOTO_FILE_RESOURCE == "auto":
        return hosts.is_openai()
    return PHOTO_FILE_RESOURCE in TRUE_WORDS

# Facts, not orders: what the tool returns, when it is the one that answers, and what
# it leaves to the host. Two failures were measured without it — an assistant asking
# the person to send back a photo it could fetch, and one calling three times for the
# same file — and each is answered by a fact ("the person does not need to send the
# photograph again", "the file stays in the conversation"). The licence sentence is
# about this file, and names no other way of making a picture.
PHOTO_FILE_DESCRIPTION = (
    "Returns one photograph from an earlier Pexafy result as an image file, identified by its `photo_id`. A result's link points to the image; this tool returns the image itself, so the person does not need to send the photograph again.\n"
    "\n"
    "Use it when the user wants a photograph found here as a file: to place it in a document, a slide or a post, to attach, send or download it, or to have it cropped, captioned or otherwise edited with the host's own tools.\n"
    "\n"
    f"The answer contains the photograph, at most {previews.PERMANENT_WIDTH} pixels wide, and a line giving its file name, size, licence, source, `photo_id`, page at the source and the credit line to display. One photo per call; the file stays in the conversation afterwards.\n"
    "\n"
    "This server does not edit images: what can be done to the file next depends on the host's own tools. Its licence and credit line apply to this photograph only."
)


# Where the bytes come from. The thumbnail proxy serves what the crawler stored and
# never upscales, and for Unsplash that is 1080 wide. Unsplash and Pexels resize on their
# own CDN, so their file is fetched from the source at w=1280, which is also what
# Unsplash's API terms ask for (image URLs used directly, "hotlinking"). Other sources
# cannot resize and their originals can be enormous, so the proxy serves them.
_RESIZING_SOURCES = ("images.unsplash.com", "images.pexels.com")


async def _photo_file_source(pid: str) -> tuple[str, str, dict]:
    """(url to fetch, file name to give it, what is known about the photograph).

    One metadata call buys two things: the widest copy every source can serve, and a
    name a person can read. Without it the attachment is called
    "019e1f64-10e3-73e7-a887-54…", which tells nobody what they are looking at.

    Falls back to the proxy for anything it cannot resolve: a name and 200 pixels are
    not worth failing a hand-over over.
    """
    fallback = (previews.sign_thumb_url_permanent(pid), f"{pid}.jpg", {})
    try:
        response = await client.get(f"/api/v1/photos/{pid}")
        response.raise_for_status()
        photo = (response.json() or {}).get("data") or {}
    except Exception:  # see the docstring
        logger.warning("Could not read %s's metadata; serving the proxy copy", pid[:12])
        return fallback

    image_url = str(photo.get("image_url") or "")
    who = re.sub(r"[^a-z0-9]+", "-",
                 str(photo.get("photographer_username") or "pexafy").lower()).strip("-")
    name = f"{who or 'pexafy'}-{pid[:8]}.jpg"

    if image_url and any(host in image_url for host in _RESIZING_SOURCES):
        base = image_url.split("?")[0]
        return f"{base}?w={previews.PERMANENT_WIDTH}", name, photo
    return (previews.sign_thumb_url_permanent(pid), name, photo)


async def get_photo_file(photo_id: str) -> ToolResult:
    if not previews.PREVIEWS_AVAILABLE:
        raise ToolError(
            "This server is not configured to hand over image files. Use the photo's "
            "`urls` instead."
        )
    pid = (photo_id or "").strip()
    if not pid:
        raise ToolError("Give the `photo_id` of a photo from a previous search.")

    url, name, photo = await _photo_file_source(pid)
    try:
        async with httpx.AsyncClient(timeout=_IMG_FETCH_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "PexafyMCP/1.0"})
            r.raise_for_status()
    except httpx.HTTPError as exc:
        # A 403 here means the id is not one of ours; anything else is the proxy. The
        # detail stays in the log: the exception names the signed proxy URL.
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else ""
        logger.warning("Could not fetch photo %s: %s %s", pid[:12], type(exc).__name__, status)
        raise ToolError(
            "Could not fetch that photo. Check the `photo_id` came from a Pexafy "
            "search result."
        ) from exc

    data = r.content
    if not data:
        raise ToolError("That photo came back empty.")
    if len(data) > PHOTO_FILE_MAX_BYTES:
        # Never silently truncate an image: a half-file is a corrupt file.
        raise ToolError(
            f"That photo is too large to hand over ({len(data) // 1024} KB). Use its "
            f"`urls.regular` link instead."
        )
    mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    encoded = base64.b64encode(data).decode()
    logger.info("Handed over photo %s as %s (%d KB)", pid[:12], mime, len(data) // 1024)

    # Three blocks. A line of text first: without it the answer is only bytes, and a
    # client that no longer holds the search result cannot say what it downloaded, nor
    # display the credit line every licence here requires.
    #
    # `ImageContent` is what the model sees, and seeing is not holding a file: given
    # only this, ChatGPT generated a lookalike when asked to put text on the photo
    # (measured). `EmbeddedResource` carries the same bytes as a resource, with a URI
    # and a MIME type — the shape MCP defines for a document, which a host can attach
    # rather than merely look at (whether ChatGPT does is not documented). The second
    # copy of the bytes is the cost, paid by ChatGPT alone unless
    # PEXAFY_PHOTO_FILE_RESOURCE says otherwise (see PHOTO_FILE_RESOURCE).
    credit = ((photo.get("attribution") or {}) if isinstance(photo, dict) else {})
    # The credit line is built from the photographer's name, which a third party wrote:
    # cleaned as it is in a search result (tooling.clean_free_text).
    said = tooling.clean_free_text(credit.get("plain")) if isinstance(credit, dict) else None
    facts = [f"{name} — {len(data) // 1024} KB, {mime}"]
    if isinstance(photo, dict):
        # The size of the file handed over, not of the original: 5808×3770 announced for
        # a file served 1280 wide is the wrong size for whoever places it in a document.
        width, height = photo.get("width"), photo.get("height")
        if width and height:
            try:
                served = min(int(width), previews.PERMANENT_WIDTH)
                facts.append(f"{served}×{round(int(height) * served / int(width))}")
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if photo.get("license_type"):
            facts.append(str(photo["license_type"]))
        if photo.get("source"):
            facts.append(str(photo["source"]))
    summary = ", ".join(facts)
    # The `photo_id` and the source page, for a client that no longer holds the search
    # result: without them it can neither call a tool again nor give a link.
    summary += f". photo_id {pid}"
    page = photo.get("source_image_url") if isinstance(photo, dict) else None
    if page:
        summary += f", source page {page}"
    # The link to the same file, for a page or a document: where the grid is shown, the
    # search answer names the photographs without one (previews.grid_summary).
    summary += f", image link {url}"
    if said:
        summary += f". Credit to display: {said}"

    blocks = [
        TextContent(type="text", text=summary),
        ImageContent(type="image", data=encoded, mimeType=mime),
    ]
    if _photo_file_as_resource():
        blocks.append(EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri=f"pexafy://photo/{name}",
                mimeType=mime,
                blob=encoded,
            ),
        ))
    return ToolResult(content=blocks)


# Every tool here reads: it searches a catalogue and returns results. A host uses
# `readOnlyHint` to decide whether a call needs the user's confirmation, and a missing
# or wrong hint is a common reason for a connector to be refused.
#
# `openWorldHint` is OpenAI's "accesses the public internet or open-ended external
# entities", and "a bounded private account or workspace isn't open-world solely because
# it is externally hosted". The two searches keep it: the by-image one fetches any URL
# it is given. The tools that only read Pexafy's own state — the file of a photo it
# returned, the grid's selection, the account link — are CLOSED_WORLD.
#
# `idempotentHint` is OpenAI's "calling the tool with the same arguments has no extra
# effect on its environment". The two searches are SEARCH, where it is false: each call
# spends one search of the caller's allowance, so the same call made twice costs twice.
# The three others keep it.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
CLOSED_WORLD = READ_ONLY.model_copy(update={"openWorldHint": False})
SEARCH = READ_ONLY.model_copy(update={"idempotentHint": False})


# The by-image tool's own inputs: three of its four references (`image_file` carries
# its description in FILE_PARAM_SCHEMA) and the words beside them. Once tuned, the spec
# operation it posts to documents only the shape filter (tooling.KEEP_PARAMS), so their
# wording is written here; the shape filter keeps the spec's own words. The grid is
# named only where there is one.
def _image_input_descriptions(*, grid: bool = True) -> dict[str, str]:
    """The by-image tool's own inputs, with the grid or without it."""
    found = (", whether it was returned in an earlier search or liked in the grid,"
             if grid else " returned in an earlier search,")
    handed = ("; where the grid hands you a short description of it instead, that "
              "description serves" if grid else "")
    return {
        "photo_id": (
            f"The photo_id of the Pexafy reference photo, copied from the result the user or assistant is referring to. It is a UUID such as `{tooling.PHOTO_ID_EXAMPLE}`. Use it instead of an image when the reference is a Pexafy photo{found} and pass the words of that search in `english_search_sentence` if needed."
        ),
        tooling.PUBLIC_QUERY_PARAM: (
            "Optional, and usable with any reference. Send the words that say what the user wants "
            "from the reference: with a `photo_id`, the words that photograph was found under, so "
            "the results stay on the subject they asked for; with an image, what they want changed "
            "or kept from it (“like this but at night”). The reference stays the main signal and "
            "these words adjust it. With an image, send the change alone (“at night”) rather than "
            "the whole scene rewritten, which would outweigh the picture instead of adjusting it; "
            "with a `photo_id`, the full sentence that photograph was found under is what to send. "
            "In English, like the sentence of a text search. The words a reference photograph was "
            f"found under are the best ones{handed}. When it is unclear whether they want the same "
            "subject or simply the same look, send the words: staying on the subject is the safer "
            "default. Omit them only when they plainly want photographs that look like the picture "
            "itself."
        ),
        "image_url": (
            "Public http(s) URL pointing directly to the reference image file, including a URL the user pasted in the conversation. For a photograph Pexafy returned, send its `photo_id` instead."
        ),
        "image_base64": (
            "Reference image as base64-encoded bytes, optionally as a data: URL. Use for programmatic clients that already hold the image data."
        ),
    }


def _spec_param_descriptions(spec: dict, path: str, method: str) -> dict[str, str]:
    """Parameter descriptions of one operation, by parameter name."""
    operation = spec.get("paths", {}).get(path, {}).get(method, {})
    found: dict[str, str] = {}
    for parameter in operation.get("parameters", []):
        text = parameter.get("description") or (parameter.get("schema") or {}).get("description")
        if text:
            found[parameter["name"]] = text
    return found


# Schema keywords the by-image tool borrows from the spec: the text search's length cap
# on the sentence. A cap stated in a description is advice, `maxLength` is what a client
# enforces, and the two tools send the same `q` to the same endpoint, so they must not
# be bounded differently.
_BORROWED_SCHEMA_KEYS = ("maxLength",)


def _to_public_names(by_param: dict) -> dict:
    """Re-key spec-derived parameter descriptions onto the names the tool exposes.

    The by-image tool borrows the wording of the spec operation it posts to, which
    indexes it under the API's names. The shape filter wears its public name on the
    tool, so a lookup by the spec name would leave it without a description.
    """
    renamed = dict(by_param)
    if tooling.API_ORIENTATION_PARAM in renamed:
        renamed[tooling.PUBLIC_ORIENTATION_PARAM] = renamed.pop(tooling.API_ORIENTATION_PARAM)
    return renamed


def _spec_param_constraints(spec: dict, path: str, method: str) -> dict[str, dict]:
    """Validation keywords of one operation's parameters, by parameter name."""
    operation = spec.get("paths", {}).get(path, {}).get(method, {})
    found: dict[str, dict] = {}
    for parameter in operation.get("parameters", []):
        schema = parameter.get("schema") or {}
        # A nullable parameter keeps its keywords on the string branch of an anyOf,
        # where apply_schema_keywords puts them.
        sources = [schema, *(b for b in schema.get("anyOf", []) if isinstance(b, dict))]
        kept = {k: src[k] for src in sources for k in _BORROWED_SCHEMA_KEYS if k in src}
        if kept:
            found[parameter["name"]] = kept
    return found


def _describe_image_tool_params(tool, from_spec: dict[str, str], *, grid: bool = True) -> None:
    """Give every parameter of the by-image tool a description.

    A Python signature carries none. The shape filter takes its wording from the spec
    operation the tool stands in for, after tooling.customize_spec has tuned it, so the
    two cannot drift; the other inputs take theirs from _image_input_descriptions, and
    `image_file` already carries its own (FILE_PARAM_SCHEMA).
    """
    own = _image_input_descriptions(grid=grid)
    for name, schema in tool.parameters.get("properties", {}).items():
        if not isinstance(schema, dict) or schema.get("description"):
            continue
        description = own.get(name) or from_spec.get(name)
        if description:
            schema["description"] = description


def _generated_tool_customizer(borrowed: dict, *, grid: bool = True):
    """Hook run on every tool FastMCP generates from the spec.

    It stamps the read-only annotations, aligns the output schema with the answer the
    server sends (tooling.prune_output_schema), and keeps `search_photos`' schema in
    `borrowed` for the hand-written by-image tool, which posts to the same endpoint and
    returns the same envelope. Captured here because it is the only synchronous sight
    of a generated tool — everything the server exposes afterwards is behind an async
    accessor.
    """

    def customize(_route, component) -> None:
        if not isinstance(component, OpenAPITool):
            return
        # The one generated tool is the text search.
        component.annotations = SEARCH.model_copy(
            update={"title": TOOL_TITLES.get(component.name)}
        )
        if component.output_schema:
            # Pruned here, before the by-image tool borrows it, so both tools declare
            # the same answer.
            component.output_schema = tooling.prune_output_schema(component.output_schema,
                                                                   grid=grid)
        if component.name == tooling.PUBLIC_TOOL_NAMES["search_photos"] and component.output_schema:
            borrowed["search_result"] = component.output_schema

    return customize


def build_server() -> FastMCP:
    """Assemble the MCP server.

    Loads the OpenAPI spec and tunes it for an LLM, generates the text search, then adds
    the hand-written tools, the middleware chain, the inline-grid MCP App resource, the
    `find_photos` prompt and the HTTP routes. Importing this module has no side
    effects: all construction, and any opt-in network, happens here.
    """
    # The grid exists only with previews configured. With no grid — no thumbnail CDN,
    # the default for a server run on its own, or PEXAFY_MCP_PREVIEWS=0 — no text names
    # it: a sentence about a grid the person does not see, or a photo "liked" in it,
    # reads perfectly well and is false.
    grid = previews.PREVIEWS_AVAILABLE
    spec = _load_openapi_spec()
    tooling.customize_spec(spec, file_tool=previews.PREVIEWS_AVAILABLE, grid=grid)
    # Filled by the hook below, while the generated tools are being built.
    borrowed_schemas: dict[str, dict] = {}
    # The selection tool reads what the reader liked in the grid. With no grid nothing
    # can be liked and there is nothing to read: the tool, and every text that names it,
    # would promise a grid that is not there. One condition for all of them.
    selection_tool = selection.TOOL_ENABLED and grid

    # Tool names derived from the FastAPI operationIds rather than hardcoded.
    mcp_names = {
        op["operationId"]: re.split(r"_api(_v1)?_", op["operationId"])[0]
        for methods in spec["paths"].values()
        for op in methods.values()
        if "operationId" in op
    }
    # A public name that differs from the derived one is set in TOOL_NAME_OVERRIDES
    # rather than by renaming the API's operationId.
    #
    # Both halves of the rename are checked, because both fail quietly. An override
    # keyed on an operationId that no longer exists never applies; and if the `_api_v1_`
    # marker disappears, `re.split` returns the whole operationId and every tool is
    # renamed at once — which breaks stored names, drops the inline grid (its `ui` meta
    # is keyed on the tool name) and loses the output schema the by-image tool borrows.
    unknown = set(TOOL_NAME_OVERRIDES) - set(mcp_names)
    if unknown:
        raise RuntimeError(
            f"OpenAPI spec drift: TOOL_NAME_OVERRIDES keys {sorted(unknown)} are no longer "
            "operationIds in the spec, so the rename never applies. Update "
            "server.TOOL_NAME_OVERRIDES to the new operationId."
        )
    mcp_names.update(TOOL_NAME_OVERRIDES)

    # The by-image tool is written by hand rather than generated, so its title is keyed
    # on a name no operationId yields. It is checked against the registry instead: the
    # point of this guard is that no title is keyed on a name nothing answers to.
    hand_written = {tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]}
    unnamed = set(TOOL_TITLES) - set(mcp_names.values()) - hand_written
    if unnamed:
        raise RuntimeError(
            f"OpenAPI spec drift: no operationId yields the tool name(s) {sorted(unnamed)} "
            f"(derived: {sorted(set(mcp_names.values()))}). Tool titles, the inline grid and "
            "the borrowed output schema are all keyed on these names."
        )

    # OAuth Resource Server, when enabled: validates Bearer tokens, resolving them to
    # the user's Pexafy API key (auth.py), and exposes RFC 9728 metadata.
    auth_provider = (
        RootAliasedAuthProvider(
            token_verifier=PexafyResolveVerifier(
                OAUTH_RESOLVE_URL, OAUTH_RESOLVE_SECRET,
                not_oauth_tokens=[METRICS_TOKEN],
            ),
            authorization_servers=[OAUTH_AS_URL],
            base_url=MCP_PUBLIC_URL,
            scopes_supported=["read"],
            resource_name="Pexafy MCP",
        )
        if OAUTH_ENABLED
        else None
    )

    # The generated surface is the text search alone; every other operation is
    # excluded: /usage (numbers surface via limit messages), /facets (values written
    # into the docs), /collections, /popular-searches, get_photo, the similar search,
    # and the POST image upload (replaced by search_photos_by_image, registered below).
    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="pexafy",
        instructions=server_instructions(file_tool=previews.PREVIEWS_AVAILABLE,
                                         selection_tool=selection_tool, grid=grid),
        # Ours, not FastMCP's. Unset, `serverInfo.version` in the initialize response
        # carries the FastMCP release, so hosts and directory scans read a version that
        # has nothing to do with this server, and upgrading the framework would look
        # like a release of it.
        # `from_openapi` forwards **settings to the FastMCP constructor.
        version=__version__,
        mcp_names=mcp_names,
        mcp_component_fn=_generated_tool_customizer(borrowed_schemas, grid=grid),
        auth=auth_provider,
        route_maps=[
            RouteMap(pattern=r"^/api/v1/usage", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/facets", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/collections", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/popular-searches", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/photos/[^/]+$", mcp_type=MCPType.EXCLUDE),
            RouteMap(methods=["POST"], pattern=r"^/api/v1/search/photos$", mcp_type=MCPType.EXCLUDE),
            # No similar tool: a catalogue photo is `photo_id` on search_photos_by_image,
            # which posts to /search/photos with the words the photo was found with.
            RouteMap(pattern=r"^/api/v1/photos/[^/]+/similar$", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r".*", mcp_type=MCPType.TOOL),
        ],
    )

    # The `_meta` of each tool: the inline-grid link (when previews are configured), the
    # grid's access to the tool, the Apps SDK file-param hint so ChatGPT injects an
    # uploaded image into `image_file`, Claude Code's always-load flag on the entry
    # point, and the status texts ChatGPT shows while a tool runs.
    def _meta_for(name: str) -> dict | None:
        meta: dict = {}
        # Every tool that returns photographs draws them in the grid. NO_GRID_TOOLS do
        # not: the connect tool returns no rows, get_photo_file's answer IS the image,
        # and the selection lists what the grid already shows. A host asked to render
        # a results widget over them has nothing to draw.
        if previews.PREVIEWS_AVAILABLE and name not in NO_GRID_TOOLS:
            meta["ui"] = {"resourceUri": widget.GRID_URI}
        if name in WIDGET_TOOLS:
            meta["openai/widgetAccessible"] = True
        if name == tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]:
            meta["openai/fileParams"] = ["image_file"]
        # Claude Code defers MCP tools behind its tool search: only their names and the
        # server instructions are loaded until one is looked up. This key loads the
        # entry point up front, description and schema included (code.claude.com/docs/en/mcp).
        if name == tooling.PUBLIC_TOOL_NAMES["search_photos"]:
            meta["anthropic/alwaysLoad"] = True
        if name in TOOL_STATUS:
            invoking, invoked = TOOL_STATUS[name]
            meta["openai/toolInvocation/invoking"] = invoking
            meta["openai/toolInvocation/invoked"] = invoked
        return meta or None

    # Titles, the `_meta` above, and the argument renames. FastMCP's ArgTransform renames
    # an argument on the tool surface and translates it back before calling the parent,
    # so the generated request still sends `orientation` and `q`. Renamed in the spec,
    # they would be renamed on the wire too, where the API does not know them — and it
    # drops an unknown filter in silence. Only the generated text search needs it
    # (RENAMED_ARGUMENT_TOOLS); the by-image tool takes the public names in its
    # signature.
    surface_renames = {
        tooling.API_ORIENTATION_PARAM: ArgTransformConfig(
            name=tooling.PUBLIC_ORIENTATION_PARAM
        ),
        tooling.API_QUERY_PARAM: ArgTransformConfig(name=tooling.PUBLIC_QUERY_PARAM),
    }

    def _transform_for(name: str, title: str) -> ToolTransformConfig:
        # `meta` stays unset when there is none: passing None marks the field as set,
        # which writes over the meta the tool already carries.
        fields: dict = {"title": title}
        meta = _meta_for(name)
        if meta:
            fields["meta"] = meta
        if name in RENAMED_ARGUMENT_TOOLS:
            fields["arguments"] = surface_renames
        return ToolTransformConfig(**fields)

    mcp.add_transform(ToolTransform({
        name: _transform_for(name, title) for name, title in TOOL_TITLES.items()
    }))

    # The by-image tool, replacing the excluded multipart POST operation. Built rather
    # than decorated so its schema can be corrected before registration: `image_file:
    # dict | None` infers a schema the Apps SDK rejects, replaced here by
    # FILE_PARAM_SCHEMA. The signature stays `dict | None`; what arrives at runtime is a
    # plain dict, read defensively.
    #
    # The return type is `ToolResult` and describes no payload, since the API shapes it.
    # The output schema is borrowed from `search_photos`: both tools post to the same
    # endpoint and return the same envelope, and a copy here would be a second source of
    # truth. Without one, a host marks the tool as returning an undeclared result.
    image_tool = FunctionTool.from_function(
        search_photos_by_image,
        name=tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
        description=image_tool_description(grid=grid),
        annotations=SEARCH.model_copy(
            update={"title": TOOL_TITLES[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]]}
        ),
        output_schema=borrowed_schemas.get("search_result"),
    )
    image_tool.parameters["properties"]["image_file"] = FILE_PARAM_SCHEMA
    # One parameter, one schema. `list[str] | None` infers an array of bare strings with
    # no enum, and the API drops an unknown shape in silence, so "vertical" would come
    # back as an unfiltered grid with no error. Replaced outright by the shared schema
    # rather than patched keyword by keyword, so the two tools cannot drift.
    image_tool.parameters["properties"][tooling.PUBLIC_ORIENTATION_PARAM] = {
        **tooling.ORIENTATION_SCHEMA, "default": None,
    }
    # The catalogue reference carries tooling.PHOTO_ID_EXAMPLE as its example (an
    # assistant that has one to copy makes fewer guesses); `q` takes the text search's
    # length cap and nothing else — an example on an optional parameter is an invitation
    # to fill it in, and this one belongs to `photo_id` alone.
    tooling.apply_schema_keywords(
        image_tool.parameters["properties"]["photo_id"],
        {"examples": [tooling.PHOTO_ID_EXAMPLE]},
    )
    search_q = _spec_param_constraints(spec, "/api/v1/search/photos", "get").get("q", {})
    if "maxLength" in search_q:
        tooling.apply_schema_keywords(
            image_tool.parameters["properties"][tooling.PUBLIC_QUERY_PARAM],
            {"maxLength": search_q["maxLength"]},
        )
    # The POST operation is excluded from tool generation (this tool replaces it), but
    # its tuned parameters still carry the shape filter's wording.
    _describe_image_tool_params(
        image_tool,
        _to_public_names(_spec_param_descriptions(spec, "/api/v1/search/photos", "post")),
        grid=grid,
    )
    mcp.add_tool(image_tool)

    # How the connect tool's probe reads the current allowance: one call to the usage
    # endpoint, which `_NO_INCREMENT_PREFIXES` exempts from the quota it reports on, so
    # asking costs nobody a search. Wired here rather than imported there: this module
    # imports linking, so linking cannot import it.
    async def _read_allowance():
        response = await client.get("/api/v1/usage")
        return budget.read(response.headers)

    linking.read_allowance = _read_allowance

    # The photograph itself, for an assistant asked to work on one rather than link
    # to it. See the note above the function.
    if previews.PREVIEWS_AVAILABLE:
        photo_file_tool = FunctionTool.from_function(
            get_photo_file,
            name=tooling.PUBLIC_TOOL_NAMES["get_photo_file"],
            description=PHOTO_FILE_DESCRIPTION,
            annotations=CLOSED_WORLD.model_copy(
                update={"title": PHOTO_FILE_TITLE}
            ),
            # No `ui.resourceUri`: the answer is the image, and a results grid drawn
            # over it would have nothing to draw.
            meta=_meta_for(tooling.PUBLIC_TOOL_NAMES["get_photo_file"]),
        )
        photo_file_tool.parameters["properties"]["photo_id"]["description"] = (
            "The `photo_id` of the specific photo to retrieve from a previous Pexafy result. It "
            f"is a UUID such as `{tooling.PHOTO_ID_EXAMPLE}`.\n\n"
            "Use the photo's `photo_id`, never a rank or a position: a rank is how the person and "
            "you name a photograph out loud, never what this parameter takes."
        )
        mcp.add_tool(photo_file_tool)

    # What the person picked in the grid, readable by the model on demand. The grid
    # publishes the same thing to the host's model-context channel on every heart;
    # this is the path that does not depend on the host reading it. See selection.py.
    # Only where there is a grid (`selection_tool`, above).
    if selection_tool:
        selected_tool = FunctionTool.from_function(
            selection.get_selected_photos,
            name=selection.SELECTED_TOOL,
            description=selection.selected_description(file_tool=previews.PREVIEWS_AVAILABLE),
            annotations=CLOSED_WORLD.model_copy(update={"title": selection.SELECTED_TITLE}),
            # No grid over it: it draws the selection the grid is already showing, and a
            # second grid under the answer would be the same photographs twice.
            meta=_meta_for(selection.SELECTED_TOOL),
        )
        # Declared: a field left undeclared is one a validating host is free to drop, and
        # the model should not have to guess the shape of this answer.
        selected_tool.output_schema = {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "selection_count": {
                    "type": "integer",
                    "description": (
                        "How many photographs they liked; `selected_photos` lists every "
                        "one."
                    ),
                },
                "selected_photos": {
                    "type": "array",
                    "description": (
                        "The photographs they liked, in their order — the order they liked "
                        "them in, unless they rearranged them in the grid. Each "
                        "carries its `rank` — #1 first — the `photo_id` to pass on, the "
                        "photographer, the source, the licence, the pixel size, the "
                        "description, the page it came from and the credit line."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer", "description": "#1 is first in their order."},
                            "photo_id": {"type": "string", "description": "What every other tool takes."},
                            "description": {"type": "string", "description": "What is in the photograph."},
                            "photographer": {"type": "string"},
                            "source": {"type": "string", "description": "The library it came from."},
                            "license": {"type": "string"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                            "orientation": {"type": "string"},
                            "url": {"type": "string", "description": "The photograph's page on Pexafy."},
                            "image_url": {"type": "string", "description": "The link to the photograph."},
                            "attribution": {"type": "string", "description": "The credit line to display."},
                        },
                        "additionalProperties": True,
                    },
                },
                "note": {
                    "type": "string",
                    "description": (
                        "What the count means, in words: a whole selection, one that a "
                        "later search replaced, or an empty one."
                    ),
                },
            },
            "required": ["success", "selection_count", "selected_photos", "note"],
        }
        mcp.add_tool(selected_tool)

    # Asking to connect an account, without having to run out first. It refuses on
    # purpose — the refusal is what carries the challenge the host turns into its own
    # button. See linking.py.
    if linking.ENABLED:
        connect_tool = FunctionTool.from_function(
            linking.connect_account,
            name=linking.CONNECT_TOOL,
            description=linking.connect_description(grid=grid),
            annotations=CLOSED_WORLD.model_copy(
                update={"title": linking.CONNECT_TITLE}
            ),
            # The status texts, and deliberately NOT `ui.resourceUri`: this tool
            # returns no photographs, and pointing it at the grid would ask the host
            # to render a results widget over an answer that has no results.
            meta=_meta_for(linking.CONNECT_TOOL),
        )
        # What the probe answers, declared like everything else this server adds to a
        # result: an undeclared field is one any host may drop, and this one is what
        # tells a grid the wall is gone.
        connect_tool.output_schema = {
            "type": "object",
            "properties": {
                "connected": {"type": "boolean",
                              "description": "Whether an account is attached to this conversation."},
                # Nullable, and null is the usual case: `budget` only appears past 80%
                # of an allowance. Declared as `object` alone, output validation would
                # fail the probe whenever the allowance is fine.
                "budget": {
                    "anyOf": [{"type": "object"}, {"type": "null"}],
                    "description": (
                        "What is left of the current allowance, or null when it is not "
                        "worth saying."
                    ),
                },
            },
        }
        # Written out, because a model reading "check_only: boolean" with no sentence
        # beside it will eventually set it — and set, this tool opens nothing, which
        # would be a connect button that does not connect. Conditional, like the tool's
        # own description says it: "is my account connected?" is a question this
        # answers, and an absolute "not by the caller" turned it into a dialog. Who else
        # sets it, the grid, is said only where there is one.
        connect_tool.parameters["properties"]["check_only"]["description"] = (
            ("Set by the Pexafy results grid: " if grid else "")
            + "`true` reports whether an account is connected — and the allowance in force — without opening the connection prompt. Leave it unset when the person wants to connect or sign in."
        )
        mcp.add_tool(connect_tool)

    # The MCP middleware chain. FastMCP runs it in the order added, the first outermost,
    # behind its own DereferenceRefsMiddleware (which inlines `$ref`s in `tools/list`).
    # From the outside in: ResolveIdentity, OrderTools, the 0.4.12 grid's window,
    # SplitPreviews, AttachOrigin, OfferAccountLink, compat (the translation, then
    # ChatGPT's similar alias), guards, observe. On a tool call the inner ones see the
    # arguments first, the outer ones see the answer last.

    # Who an anonymous caller is. Outermost of ours, so everything below — the guards,
    # the tools, the outgoing HTTP hook — reads the identity it puts in context.
    mcp.add_middleware(anonymous.ResolveIdentity())

    # Which tool the router reads first. See OrderTools.
    mcp.add_middleware(OrderTools())

    # While ChatGPT may still draw answers with the 0.4.12 grid it keeps in its cache
    # (PEXAFY_LEGACY_GRID_UNTIL), an OpenAI host's answer keeps what that grid reads.
    # Outside SplitPreviews and AttachOrigin: it puts back, in `structuredContent` alone,
    # what they take out of it. See compat.py.
    mcp.add_middleware(compat.KeepLegacyGrid())
    compat.log_legacy_grid_window()

    # The outermost of the middlewares that shape a result (the window above only puts
    # back, for the 0.4.12 grid, what this one takes out), so it sees the finished answer
    # whoever built it — the generated search, the by-image one, a wall — and moves the
    # grid's image links off the model's half of it. See previews.PREVIEW_CHANNEL.
    mcp.add_middleware(previews.SplitPreviews())

    # …and what the frame needs to ask the same question again with a shape attached.
    # Its own middleware, because SplitPreviews returns early on an answer with no
    # image links, and an empty grid is precisely the one the frame must be able to
    # re-ask from. It reads `context.message` after `call_next`, once compat (further
    # in) has rewritten it in place, so the origin carries the current tool and argument
    # names whatever the caller sent. See origin.py.
    mcp.add_middleware(origin.AttachOrigin())

    # "Anonymous first, link later". `securitySchemes` on every tool tells the host the
    # connector works with no account AND accepts one. The middleware turns a BudgetWall
    # raised below (connect_account, or a spent allowance when the wall is not drawn as
    # a grid) into an error result, carrying the connect challenge where linking.py
    # allows it. Inside SplitPreviews and AttachOrigin, so that result passes through
    # them like any answer. See linking.py.
    mcp.add_middleware(linking.OfferAccountLink(MCP_PUBLIC_URL))
    linking.declare_optional_auth()

    # A host on an older snapshot still calls retired tool names and sends retired
    # filters: the names are translated and the filters dropped, so the call answers
    # instead of failing on "unexpected keyword argument". See compat.py.
    mcp.add_middleware(compat.DropRetiredParams())
    # Arguments the API would answer badly are refused here, with a message the caller
    # can act on: a `photo_id` that is not one (a rank), a blank or overlong sentence,
    # an unknown shape. After compat, so they see the current names. See guards.py.
    mcp.add_middleware(guards.GuardPhotoId())
    # One log line per message that gets this far: which `_meta` keys the host sent, and
    # a digest of `openai/subject`, the one that identifies a caller for the allowance.
    # Innermost, so a call the guards refuse has no line here (the guard logs its own).
    # Logs only; see observe.py.
    mcp.add_middleware(observe.LogRequestMeta())

    # Inline result-grid MCP App resource — only when the thumbnail CDN is configured.
    if previews.PREVIEWS_AVAILABLE:
        # The thumbnail CDN to load pictures from, this server to post the selection
        # to, and nothing else. See _grid_csp_domains.
        resource_domains, connect_domains = _grid_csp_domains()
        mcp.resource(
            widget.GRID_URI,
            name="pexafy_results_grid",
            title="Pexafy results grid",
            description=(
                "The photographs found, as a grid the reader works in: unnumbered thumbnails, a heart "
                "on each to like it, a shape filter in the head, and, on a tap, a swipe deck in the "
                "grid's own place — one photograph at a time, with its credit, license, kept or "
                "skipped with a swipe, and a similar button (the ≈ mark) for more photographs like "
                "it. The filter and ≈ re-run the search inside the grid, where the host lets the "
                "frame call a tool. The photos they liked stay under the grid, numbered #1..#n."
            ),
            mime_type=UI_MIME_TYPE,
            # Drawn FOR the reader: the annotation tells a host that the grid is rendered,
            # and a model that it need not read it back as text.
            annotations={"audience": ["user"]},
            app=AppConfig(
                csp=ResourceCSP(
                    resource_domains=resource_domains,
                    connect_domains=connect_domains,
                ),
                # The spec's field, in the spec's format — see _spec_ui_domain.
                # `openai/widgetDomain` below carries ours, under the name ChatGPT
                # reads; the two are different things.
                domain=_spec_ui_domain() or None,
            ),
            # The same two facts under the names ChatGPT reads. FastMCP writes the
            # spec form (`_meta.ui.*`); these are the compatibility aliases, built from
            # the same values so the two encodings cannot drift. The domain is required
            # by the app portal, and the CSP alias is what decides whether thumbnails
            # load once the app is published rather than previewed.
            meta={
                "openai/widgetDomain": WIDGET_DOMAIN,
                # What the model reads when the grid loads: the surface the reader is
                # working in, so it stops re-listing in text what is already on screen.
                "openai/widgetDescription": (
                    "A grid of the photographs found, drawn for the reader: thumbnails with no numbers, a "
                    "heart on each to like it, a shape filter that re-runs the search, and a swipe deck "
                    "that opens in the grid's own place — one photograph at a time, with who took it and "
                    "what is in it, and a ≈ button that asks for more like it. The photographs the reader "
                    "liked stay under the grid, numbered #1..#n on screen in the reader's order — the "
                    "order liked, which they can rearrange by dragging: those are "
                    "the numbers the reader sees and names them by (“#2”). "
                    + (f"They are readable through {tooling.PUBLIC_TOOL_NAMES['get_selected_photos']}. "
                       if selection_tool else "The grid passes them to you in its context. ")
                    + "The grid already shows the photographs themselves."
                ),
                "openai/widgetCSP": {
                    "resource_domains": resource_domains,
                    "connect_domains": connect_domains,
                },
            },
        )(_grid_html)
        # `_meta.ui.domain` as registered is Claude's, and only a Claude host is served
        # it: every other one gets the origin OpenAI reads that field as. See hosts.py.
        mcp.add_middleware(hosts.WidgetDomainPerHost(widget.GRID_URI, WIDGET_DOMAIN))
        # The site's logo on the name, for the hosts that show none above the frame
        # (VS Code, Cursor…); never in the page ChatGPT or Claude reads.
        mcp.add_middleware(hosts.LogoWhereTheHostDrawsNone(widget.GRID_URI, widget.with_logo))
        logger.info("Inline result-grid MCP App ON — %s (SDK inlined=%s, thumbs %s)",
                    widget.GRID_URI, widget.SDK_INLINED, previews.THUMB_ORIGIN)

    # A prompt the PERSON invokes, not the model: Claude Code lists it as
    # /mcp__pexafy__find_photos, Claude.ai puts it in the connector's "+" menu, and the
    # OpenAI scan stores it. It is the one surface here that answers "what can this
    # connector do" without a search being attempted first.
    @mcp.prompt(
        name="find_photos",
        title="Find stock photos",
        description=(
            "Find free stock photographs of a scene you describe, in any language, with "
            "their credit lines."
        ),
    )
    def find_photos(scene):
        """Find free stock photographs of a scene you describe.

        The argument is left unannotated on purpose. This module uses
        `from __future__ import annotations`, so an annotation reaches FastMCP as the
        STRING "str": its `annotation is not str` test is then true, and it wraps the
        description in "Provide as a JSON string matching the following schema" — the
        only text the person reads in the "+" menu. Unannotated, the description below
        passes through as written.

        Args:
            scene: The picture you need, in your own words: subject, setting, light.
        """

        return (
            f"Find free stock photographs of: {scene}. Where the Pexafy grid is not "
            "shown, show them with their credit lines; then offer more like one of them."
        )

    # Domain-ownership challenge for OpenAI's app directory. Their check fetches
    # /.well-known/openai-apps-challenge on the host serving this MCP server and
    # expects the bare token back — no JSON, no wrapper. Served only when the token
    # is configured, so the path 404s rather than returning an empty body when it
    # is not, which would read as "verified with nothing".
    challenge = os.environ.get("OPENAI_APPS_CHALLENGE", "").strip()
    if challenge:
        @mcp.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])
        async def openai_apps_challenge(_request: Request) -> PlainTextResponse:
            return PlainTextResponse(challenge)

        logger.info("OpenAI app-directory domain challenge served at /.well-known/openai-apps-challenge")

    # Pre-connect metadata for directories that cannot get past the auth wall.
    # `initialize` and `tools/list` need a token, correctly so — an OAuth server SHOULD
    # answer 401 so the flow is discovered — but that leaves a scanner with nothing to
    # show. This path is the documented escape hatch for when automatic scanning cannot
    # complete behind an auth wall, and it is what the pre-connect discovery draft
    # (SEP-2127) and a growing number of directory probes look for.
    #
    # Generated from the live server, never hand-written: the tools carry their own
    # wire-format schemas, so the card cannot drift from what `tools/list` returns.
    # Public: it is the tool catalogue every client receives on connect, and it names
    # no user and no credential.
    @mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
    async def server_card(_request: Request) -> JSONResponse:
        tools = [
            tool.to_mcp_tool().model_dump(mode="json", exclude_none=True)
            for tool in await _listed(mcp, "tools")
        ]
        resources = [
            resource.to_mcp_resource().model_dump(mode="json", exclude_none=True)
            for resource in await _listed(mcp, "resources")
        ]
        prompts = [
            prompt.to_mcp_prompt().model_dump(mode="json", exclude_none=True)
            for prompt in await _listed(mcp, "prompts")
        ]
        return JSONResponse(
            {
                "serverInfo": {
                    "name": mcp.name,
                    "title": SERVER_TITLE,
                    "version": __version__,
                },
                # `oauth2` alone: the API-key path this server also accepts is for
                # no-code callers holding their own key, not something a directory
                # can discover or be issued. Not required when anonymous access is
                # on: every tool then also declares `noauth` (linking.py).
                "authentication": {
                    "required": OAUTH_ENABLED and not anonymous.is_configured(),
                    "schemes": ["oauth2"] if OAUTH_ENABLED else [],
                },
                "tools": tools,
                "resources": resources,
                "prompts": prompts,
            }
        )

    # Answer the sessionless 2026-07-28 discovery probe before the SDK builds a
    # transport it will never be able to reach again. See sessions.py — the reply
    # is the one the SDK already sends, so no client behaviour changes.
    sessions.install()

    # Prometheus scrape, guarded by PEXAFY_METRICS_TOKEN_FILE when one is configured
    # (a deployment should also keep the path off the public edge).
    #
    # One number matters here: how many Streamable-HTTP sessions the process is holding.
    # The SDK creates one per client connection and only drops it when the client says
    # goodbye, which few of them do — so this curve is what decides whether an idle
    # timeout is worth the reconnections it would cost. `/health` stays a liveness
    # probe; this is the gauge.
    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(request: Request) -> PlainTextResponse:
        if METRICS_TOKEN:
            offered = request.headers.get("Authorization", "")
            scheme, _, credential = offered.partition(" ")
            # compare_digest, not ==: a token is a secret, and the timing of a
            # string comparison tells an attacker how much of it they guessed. On
            # bytes: given a non-ASCII `str` it raises, which would answer a 500.
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                credential.strip().encode(), METRICS_TOKEN.encode()
            ):
                return PlainTextResponse("Unauthorized", status_code=401)
        lines = []
        open_count = sessions.open_sessions()
        if open_count is None:
            # sessions.py has already logged why. Emit nothing rather than a zero,
            # which a dashboard would draw as "all is well".
            lines.append("# pexafy_mcp_sessions_open unavailable — see server log")
        else:
            lines += [
                "# HELP pexafy_mcp_sessions_open Streamable-HTTP sessions held in memory.",
                "# TYPE pexafy_mcp_sessions_open gauge",
                f"pexafy_mcp_sessions_open {open_count}",
            ]
        lines += [
            "# HELP pexafy_mcp_discovery_probes_total Sessionless server/discover probes "
            "answered without allocating a transport.",
            "# TYPE pexafy_mcp_discovery_probes_total counter",
            f"pexafy_mcp_discovery_probes_total {sessions.probes_short_circuited()}",
        ]
        return PlainTextResponse(
            "\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # Where the grid writes what the reader picked. See selection.py for why this
    # exists at all: the host's own channel for model-visible UI state is documented,
    # used here, and unreliable — so the selection also travels a path that does not
    # depend on the host reading anything, and the selection tool reads it back.
    #
    # No credential: an iframe has none to give, and one handed to it would be a
    # credential published to every reader. The write capability is the signed token
    # that rode out on the answer this grid is rendering, and it can only name the
    # caller that answer was for. `text/plain` rather than JSON, because a JSON
    # content type turns this into a preflighted request and the answer to an OPTIONS
    # the SDK never sees is a channel that silently does nothing.
    @mcp.custom_route("/selection", methods=["POST", "OPTIONS"])
    async def put_selection(request: Request) -> JSONResponse:
        cors = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Max-Age": "86400",
        }
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=cors)
        # The declared size is checked before a byte is read; the body is measured
        # again after, for a request that declared none.
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > selection.MAX_BODY:
            return JSONResponse({"error": "too large"}, status_code=413, headers=cors)
        raw = await request.body()
        if len(raw) > selection.MAX_BODY:
            return JSONResponse({"error": "too large"}, status_code=413, headers=cors)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400, headers=cors)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad body"}, status_code=400, headers=cors)
        key = selection.read_token(str(body.get("token") or ""))
        if not key:
            # Nothing about WHY: a token that is forged, stale or from another server
            # all read the same from outside, which is the point.
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=cors)
        try:
            revision = int(body.get("revision") or 0)
        except (TypeError, ValueError, OverflowError):
            return JSONResponse({"error": "bad revision"}, status_code=400, headers=cors)
        entry = selection.put(key, body.get("items"), revision,
                              frame=str(body.get("frame") or ""))
        logger.info("selection stored count=%d revision=%s", entry["count"], entry["revision"])
        return JSONResponse({"ok": True, "count": entry["count"],
                             "revision": entry["revision"]}, headers=cors)

    # The grid's view, for a host that keeps none (selection.put_view). Same terms as
    # /selection: no credential, `text/plain`, and the permission is the signed token
    # the answer carried. With `view` in the body it is kept; without, it is read back.
    @mcp.custom_route("/view", methods=["POST", "OPTIONS"])
    async def grid_view(request: Request) -> JSONResponse:
        cors = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Max-Age": "86400",
        }
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=cors)
        limit = selection.VIEW_MAX + 4096                 # the view and its envelope
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            return JSONResponse({"error": "too large"}, status_code=413, headers=cors)
        raw = await request.body()
        if len(raw) > limit:
            return JSONResponse({"error": "too large"}, status_code=413, headers=cors)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400, headers=cors)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad body"}, status_code=400, headers=cors)
        key = selection.view_key(str(body.get("token") or ""))
        if not key:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=cors)
        sig = body.get("sig")
        if not isinstance(sig, str) or not sig or len(sig) > selection.VIEW_SIG_MAX:
            return JSONResponse({"error": "bad sig"}, status_code=400, headers=cors)
        if "view" in body:
            if not selection.put_view(key, sig, body.get("view")):
                return JSONResponse({"error": "bad view"}, status_code=400, headers=cors)
            return JSONResponse({"ok": True}, headers=cors)
        view = selection.get_view(key, sig)
        logger.info("grid view read found=%s", view is not None)
        return JSONResponse({"view": view}, headers=cors)

    # The grid says it has just shown the coach, which plays once per person
    # (coach.py). Same terms as /selection: no credential, `text/plain`, and the
    # permission is the signed token the answer carried, which names the person.
    @mcp.custom_route("/coach", methods=["POST", "OPTIONS"])
    async def coach_seen(request: Request) -> JSONResponse:
        cors = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Max-Age": "86400",
        }
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=cors)
        raw = await request.body()
        if len(raw) > 2048:
            return JSONResponse({"error": "too large"}, status_code=413, headers=cors)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return JSONResponse({"error": "bad json"}, status_code=400, headers=cors)
        person = selection.read_token(str(body.get("token") or "")) if isinstance(body, dict) else ""
        if not person:
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers=cors)
        await coach.mark(person)
        logger.info("coach shown: not again for this person")
        return JSONResponse({"ok": True}, headers=cors)

    # The grid, over HTTP, for a page that is not a chat.
    #
    # The widget is an MCP resource: hosts read it over the protocol and there is no URL
    # to put in a `src`. A browser page that wants to show the same grid — the public
    # try-it page — therefore has to be handed the same bytes another way. It is the same HTML either way, built once in widget.py, so the two
    # can never drift.
    #
    # Public, and there is nothing in it to protect: markup, CSS and script, no key, no
    # catalogue. What it can DO is bounded by the CSP below — the same origins the MCP
    # resource declares, each for the same use (pictures from the thumbnail CDN, `fetch`
    # to this server alone: _grid_csp_domains), plus the frame-ancestors a resource
    # served over the protocol never needs.
    @mcp.custom_route("/widget", methods=["GET"])
    async def widget_html(_request: Request) -> HTMLResponse:
        resource_domains, connect_domains = _grid_csp_domains()
        images = " ".join([*resource_domains, "data:", "blob:"])
        media = " ".join(resource_domains) or "'none'"
        connect = " ".join(connect_domains) or "'none'"
        ancestors = os.environ.get(
            "PEXAFY_WIDGET_FRAME_ANCESTORS",
            "https://pexafy.com https://www.pexafy.com https://preprod.pexafy.com "
            "http://localhost:8000 http://127.0.0.1:8000",
        )
        return HTMLResponse(widget.GRID_HTML, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'unsafe-inline'; font-src data:; "
                f"img-src {images}; media-src {media}; "
                f"connect-src {connect}; base-uri 'none'; form-action 'none'; "
                f"frame-ancestors {ancestors}"
            ),
        })

    # Liveness/readiness probe (no auth) — reports the live tool count. Public, so a
    # failure is named in the log and not in the answer.
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        try:
            tool_count = len(await _listed(mcp, "tools"))
        except Exception as exc:
            logger.warning("Health check could not list tools: %r", exc)
            return JSONResponse({"status": "degraded", "error": "could not list tools"},
                                status_code=503)
        return JSONResponse(
            {"status": "ok", "tools": tool_count, "oauth": OAUTH_ENABLED, "transport": TRANSPORT}
        )

    # The connector's icon. An assistant draws a connector with the favicon of its
    # address: mcp.pexafy.com answers it from Caddy, mcp.preprod.pexafy.com answered 404
    # and ChatGPT drew a broken image for the preprod connector (2026-10-01). Served
    # here, the same mark answers on every address this server is given.
    @mcp.custom_route("/favicon.ico", methods=["GET"])
    @mcp.custom_route("/favicon.svg", methods=["GET"])
    async def favicon(_request: Request) -> Response:
        return Response(FAVICON_SVG, media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})

    logger.info("Pexafy MCP ready — transport=%s oauth=%s api=%s", TRANSPORT, OAUTH_ENABLED, API_BASE_URL)
    return mcp


_USAGE = """\
pexafy-mcp — MCP server for Pexafy image search.

Usage:
  pexafy-mcp            Start the server (transport from PEXAFY_MCP_TRANSPORT, default: stdio)
  pexafy-mcp --help     Show this help
  pexafy-mcp --version  Show the version

Configuration is via environment variables — see .env.example. Key ones:
  PEXAFY_API_BASE_URL    Pexafy API root (default http://localhost:8000)
  PEXAFY_MCP_TRANSPORT   "stdio" (Claude Desktop/Code) or "http" (remote)
  PEXAFY_MCP_HOST/PORT   bind address for the http transport (default 127.0.0.1:8765)
"""


def http_app(mcp: FastMCP):
    """The ASGI app this server runs, with anonymous access wrapped AROUND it.

    `mcp.run(middleware=…)` is not enough for AllowAnonymous, and the reason is worth
    writing down because it fails silently. FastMCP appends custom middleware to the
    END of its stack (`create_streamable_http_app`: `server_middleware.extend(middleware)`),
    after the authentication middleware it installs itself — so a request with no
    `Authorization` header has already been rejected by the time custom middleware sees
    it. Listed first or last in HTTP_MIDDLEWARE changes nothing; the list itself is in
    the wrong place.

    Wrapping the finished app is the only position that is genuinely outside the auth,
    which is where a middleware whose whole job is "let this request in without a
    credential" has to be.
    """
    return anonymous.AllowAnonymous(mcp.http_app(middleware=HTTP_MIDDLEWARE),
                                    sign_in_by_401=OAUTH_ENABLED)


def main() -> None:
    """Console-script entry point (`pexafy-mcp`).

    Transport is configurable: "stdio" (default, for Claude Desktop/Code) or
    "http" (Streamable HTTP, for a remote service). Host/port via env.
    """
    import sys

    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(_USAGE)
        return
    if any(a in ("-V", "--version") for a in args):
        print(f"pexafy-mcp {__version__}")
        return

    mcp = build_server()
    if TRANSPORT == "http":
        import uvicorn

        uvicorn.run(http_app(mcp), host=MCP_HOST, port=MCP_PORT,
                    log_config=uvicorn_log_config())
    else:
        mcp.run()  # stdio


def uvicorn_log_config() -> dict:
    """uvicorn's own logging configuration, with the caller's address left out.

    Its access line starts with `client_addr`: the reverse proxy's address — or, once
    uvicorn trusts that proxy's `X-Forwarded-For` (FORWARDED_ALLOW_IPS), the person's
    own, in clear, on every request. The line keeps the method, the path and the status;
    the address goes. This server reads an address for one purpose, naming an anonymous
    caller, and never writes it anywhere (anonymous.py).
    """
    import copy

    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["access"]["fmt"] = '%(levelprefix)s "%(request_line)s" %(status_code)s'
    return config


if __name__ == "__main__":
    main()
