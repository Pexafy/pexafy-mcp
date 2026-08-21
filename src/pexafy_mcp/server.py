"""Pexafy MCP server — a thin MCP client of the Pexafy image-search API.

The server is generated from the Pexafy public OpenAPI spec (`{API}/schema.json`):
FastMCP derives the tools — names, parameters, types and the response schema — so
the API stays the single source of truth. This module then wires everything an LLM
client needs on top:

  - tooling.py   tunes the generated tools for an LLM (descriptions, closed value
                 sets, dropped params) and narrows the surface to the search core;
  - a custom `search_photos_by_image` tool that takes an image URL (a chat assistant
                 cannot upload a binary file to an MCP tool);
  - widget.py    an MCP Apps UI resource that renders results as an inline grid;
  - previews.py  signs thumbnail URLs injected into each result for that grid;
  - limits.py    turns plan-limit (429) responses into in-chat upgrade nudges;
  - auth.py      per-user auth: OAuth Resource Server or a forwarded API key;
  - sessions.py  answers the 2026-07-28 discovery probe before the SDK allocates a
                 transport for it, and counts the sessions held in memory;
  - unauthorized.py  puts a readable explanation in the otherwise empty 401.

Entry point: `pexafy-mcp` (console script) → ``main()``. Transport is `stdio`
(Claude Desktop/Code) or `http` (remote Streamable HTTP), selected by env.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

# Load .env BEFORE importing fastmcp and the local modules below — all of them read
# configuration at import time. find_dotenv walks up from the current working
# directory, so a .env in the user's project is picked up; in Docker the variables
# come from the environment instead.
load_dotenv()

# Keep the server fully offline by default: FastMCP otherwise pings PyPI on startup
# (its CLI "check for updates"), an unexpected outbound call for a stdio/air-gapped
# server. `fastmcp.settings` is built at import, so this must be set BEFORE importing
# fastmcp. setdefault preserves an explicit opt-in (FASTMCP_CHECK_FOR_UPDATES in env/.env).
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")

import httpx  # noqa: E402 — must follow the env setup above
from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.server.dependencies import get_access_token, get_http_headers  # noqa: E402
from fastmcp.apps import AppConfig, ResourceCSP, UI_MIME_TYPE  # noqa: E402
from fastmcp.server.providers.openapi import MCPType, OpenAPITool, RouteMap  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from fastmcp.server.transforms import ToolTransform  # noqa: E402
from fastmcp.tools.function_tool import FunctionTool  # noqa: E402
from fastmcp.tools.tool import ToolResult  # noqa: E402
from fastmcp.tools.tool_transform import ToolTransformConfig  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, PlainTextResponse  # noqa: E402

from . import __version__  # noqa: E402
from . import previews  # noqa: E402 — must follow load_dotenv (reads env at import)
from . import limits  # noqa: E402
from . import sessions  # noqa: E402
from . import tooling  # noqa: E402
from . import unauthorized  # noqa: E402
from . import widget  # noqa: E402
from .auth import (  # noqa: E402
    API_KEY_CLAIM,
    QUOTA_MESSAGE_CLAIM,
    PexafyResolveVerifier,
    RootAliasedAuthProvider,
)

# --- Observability ----------------------------------------------------------
# Logs go to stdout (captured by `docker compose logs`). Level via env so prod
# can run INFO and a debug session can drop to DEBUG without a code change.
logging.basicConfig(
    level=os.environ.get("PEXAFY_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("pexafy.mcp")

# Static assets shipped with the package — produced at build time by prepare.sh
# (the OpenAPI snapshot and the evolving facet value sets), so importing this module
# performs NO network I/O.
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

# OAuth (Resource Server): when these are set in HTTP transport, the MCP server
# requires a Bearer OAuth token, resolves it to the user's Pexafy API key via the
# Django Authorization Server, and calls the API with that key. See auth.py.
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

# Bearer token guarding /metrics, as every other Pexafy service guards its own:
# Prometheus reads the same secret from a file (`credentials_file` in its scrape
# config), so the deployment points both at one token rather than copying it. Left
# empty — a local run, a stdio client — the endpoint is open, which is what a
# developer wants and what a deployment must not do.
METRICS_TOKEN_FILE = os.environ.get("PEXAFY_METRICS_TOKEN_FILE", "")
METRICS_TOKEN = (
    Path(METRICS_TOKEN_FILE).read_text().strip()
    if METRICS_TOKEN_FILE
    else os.environ.get("PEXAFY_METRICS_TOKEN", "").strip()
)

# Starlette middleware the remote server runs. One entry: the 401 an
# unauthenticated caller receives is empty by default, and a person whose client
# does not speak OAuth has no way to learn that a plain Pexafy API key works.
# Passed to `http_app()`/`run()`, so it wraps the route that issues the 401.
HTTP_MIDDLEWARE = [Middleware(unauthorized.UnauthorizedHint)]

# Human-facing name, shown by directories that read the server card. `mcp.name`
# beside it is the protocol identifier ("pexafy"); a listing built from that reads
# like a package, not a product. tests/test_server_card.py pins this to the `title`
# in server.json, which is what the MCP registry publishes.
SERVER_TITLE = "Pexafy"

# The origin the inline grid is attributed to. Nothing is served from it: ChatGPT
# renders the widget under `<widgetDomain>.web-sandbox.oaiusercontent.com`, so the
# value is an isolation label — one origin per app, which is why its portal
# requires it and refuses the shared default. It is also where "Open in Pexafy"
# sends someone from the fullscreen view, which is why it is the website and not
# the MCP host: the latter would land them on an endpoint, not on a page.
WIDGET_DOMAIN = os.environ.get("PEXAFY_WIDGET_DOMAIN", "https://pexafy.com")


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


def _load_facets() -> dict[str, list[str]]:
    """Source/license value sets from the vendored snapshot (assets/facets.json).
    Orientations and colors are static constants in tooling.py."""
    try:
        return json.loads((_ASSETS / "facets.json").read_text())
    except Exception as exc:  # noqa: BLE001 — fall back to the minimal static set
        logger.warning("facets.json unavailable (%s) — using fallback values", exc)
        return dict(tooling.FALLBACK_FACETS)


# Per-request key selection, in priority order:
#   1. OAuth (HTTP + auth enabled): the token was validated by PexafyResolveVerifier,
#      which stashed the user's resolved Pexafy API key in the token claims. Use it.
#   2. Direct per-user key (HTTP, no OAuth): the client passes its OWN Pexafy key
#      (`x-api-key` or `Authorization: Bearer <key>`); forward it.
#   3. stdio (local Claude Desktop/Code): no HTTP request → fall back to env API_KEY.
async def _forward_client_key(request: httpx.Request) -> None:
    # 1) OAuth-resolved key from the validated token's claims.
    token = get_access_token()
    if token is not None and token.claims:
        # Plan-limit block: no key could be minted. Surface a sales message as the
        # tool error (shown in-chat) instead of silently failing the API call.
        blocked = token.claims.get(QUOTA_MESSAGE_CLAIM)
        if blocked:
            logger.info("Tool call blocked by plan limit for %s", token.client_id)
            raise ToolError(blocked)
        resolved = token.claims.get(API_KEY_CLAIM)
        if resolved:
            request.headers["x-api-key"] = resolved
            logger.debug("Auth: OAuth-resolved key for %s", token.client_id)
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
    else:
        logger.debug("Auth: no client key — falling back to env API_KEY")


# Keep the inline result grid visually coherent: default a search page to 12 photos
# and hard-cap it at 20 (the widget also slices to 20). Applies to the grid tools
# (text/image search + similar); `cursor` paging still works for more. The page size
# is FIXED, not just defaulted: a uniform 4×4 grid is the whole UX — a search that
# returned 5 or 7 photos looked broken — so we force it here regardless of anything the
# assistant passes (`per_page`/`limit` are also hidden from the tools). We do NOT touch
# score_threshold: the engine's own relevance cut-off stays in charge of how many photos
# are good enough to show — we never pad the grid with weak matches.
GRID_PAGE_SIZE = 16


async def _grid_page_size(request: httpx.Request) -> None:
    path = request.url.path
    if "/search/photos" not in path and "/similar" not in path:
        return
    request.url = request.url.copy_set_param("per_page", str(GRID_PAGE_SIZE))


# Turn the API's plan-limit responses (HTTP 429: monthly quota / rate limit) into a
# friendly upgrade nudge shown in-chat, rather than a raw "HTTP error 429" dump.
async def _surface_plan_limits(response: httpx.Response) -> None:
    """Turn an API plan-limit 429 into a metric upgrade nudge shown in-chat.

    All the numbers come from the response headers (X-Plan, X-Quota-Limit,
    X-RateLimit-Limit) — no extra usage call needed.

    Rate-limit vs monthly-quota MUST be told apart correctly: a monthly-quota
    block must NOT tell the assistant to "wait and retry" (no wait clears it),
    or it retries in a futile loop. Both 429s carry the same headers (and the
    API historically set Retry-After on monthly blocks too), so the only reliable
    discriminator is the machine-readable `error.code` in the JSON body
    (RATE_LIMITED vs QUOTA_EXCEEDED). The Retry-After header is a fallback for a
    proxy that strips the body.
    """
    if response.status_code != 429:
        return
    plan = response.headers.get("X-Plan", "")
    code = ""
    try:
        body = json.loads(await response.aread())
        if isinstance(body, dict):
            code = (body.get("error") or {}).get("code", "") or ""
    except Exception:  # noqa: BLE001 — fall back to the header heuristic
        code = ""
    if code == "QUOTA_EXCEEDED":
        is_rate = False
    elif code == "RATE_LIMITED":
        is_rate = True
    else:
        # No code (proxy stripped the body): a positive Retry-After means rate limit.
        is_rate = "retry-after" in response.headers
    if is_rate:
        logger.info("Plan limit surfaced: rate-limit (plan=%s)", plan or "?")
        raise ToolError(await limits.rate_limit_message(
            plan, response.headers.get("X-RateLimit-Limit"), response.headers.get("Retry-After")))
    logger.info("Plan limit surfaced: monthly quota (plan=%s)", plan or "?")
    raise ToolError(await limits.monthly_quota_message(plan, response.headers.get("X-Quota-Limit")))


# On a successful search body: inject a signed `preview_url` per photo (read once,
# Public Pexafy website — the grid footer links back here, contextual to the search.
PEXAFY_WEB = os.environ.get("PEXAFY_WEB_URL", "https://pexafy.com").rstrip("/")


def _pexafy_cta(request: httpx.Request) -> dict | None:
    """Context-aware call-to-action for the grid footer, derived from the request:
    - text search  → 'More at Pexafy' → the live results page (/?q=…)
    - similar       → 'More at Pexafy' → the reference photo page (/photos/{id}/)
    - image search  → 'Try it at Pexafy' (no public URL to mirror an uploaded image)
    """
    path = request.url.path
    m = re.search(r"/photos/([^/]+)/similar", path)
    if m:
        return {"label": "More at Pexafy", "url": f"{PEXAFY_WEB}/photos/{m.group(1)}/"}
    if path.endswith("/search/photos"):
        if request.method.upper() == "POST":  # by-image (or image+text) search
            return {"label": "Try it at Pexafy", "url": PEXAFY_WEB}
        q = request.url.params.get("q")
        if q:
            return {"label": "More at Pexafy", "url": f"{PEXAFY_WEB}/?q={quote_plus(q)}"}
        return {"label": "More at Pexafy", "url": PEXAFY_WEB}
    return None


# mutate, write back). The preview_url feeds the inline result grid widget (see
# widget.py / previews.py); it never carries the HMAC secret. Internal fields are
# already stripped upstream in the Pexafy public schema, so nothing to prune here.
async def _enrich_response(response: httpx.Response) -> None:
    if response.status_code >= 400:
        return
    if "json" not in response.headers.get("content-type", ""):
        return
    try:
        data = json.loads(await response.aread())
    except Exception:  # noqa: BLE001 — leave non-JSON / unparseable bodies untouched
        return
    previews.inject_ranks(data)         # number results #1..#N (user-facing handle)
    previews.inject_preview_urls(data)  # add signed preview_url for the grid
    cta = _pexafy_cta(response.request)  # contextual "More/Try at Pexafy" grid footer link
    if cta and isinstance(data, dict):
        data["cta"] = cta
    new = json.dumps(data).encode()
    response._content = new  # already fully read & cached; downstream reads see the new body
    try:
        response.headers["content-length"] = str(len(new))
    except Exception:  # noqa: BLE001
        pass


# HTTP client: env API_KEY is the default/fallback (stdio); the request hook overrides
# it with the calling client's key when present (HTTP). X-Source tags analytics.
# `retries` only retries connection-level failures (DNS, refused, reset) — never a
# received HTTP response — so it absorbs transient blips without re-sending on a 4xx/5xx.
HTTP_RETRIES = int(os.environ.get("PEXAFY_HTTP_RETRIES", "2"))
client = httpx.AsyncClient(
    base_url=API_BASE_URL,
    headers={"x-api-key": API_KEY, "X-Source": SOURCE_HEADER},
    timeout=HTTP_TIMEOUT,
    transport=httpx.AsyncHTTPTransport(retries=HTTP_RETRIES),
    event_hooks={
        "request": [_forward_client_key, _grid_page_size],
        # _surface_plan_limits raises on 429 (returns early otherwise); enrich runs on 2xx.
        "response": [_surface_plan_limits, _enrich_response],
    },
)

# ── Tool & resource definitions (assembled into a server by build_server) ─────

# Display titles shown by the client (the snake_case names stay the call ids).
TOOL_TITLES = {
    "search_photos": "Search photos by description",
    "search_photos_by_image": "Search photos by example image",
    "get_similar_photos": "Find similar photos",
}

# Tool names are generated from the API's operationIds. Where the result reads as
# a noun rather than an action, it is overridden here.
TOOL_NAME_OVERRIDES = {
    "photo_similar_api_v1_photos__photo_id__similar_get": "get_similar_photos",
}


def _grid_html() -> str:
    """Body of the inline result-grid MCP App resource (see widget.py)."""
    return widget.GRID_HTML


# Search-by-image, URL edition. The OpenAPI POST tool (excluded in build_server)
# takes a multipart file, which a chat assistant can't supply. This custom tool
# takes an image URL the assistant CAN pass, fetches it server-side, and posts a
# real multipart request to the same API endpoint via `client` — so the existing
# auth, per_page and response-enrichment hooks all apply, and the grid renders.
_IMG_FETCH_TIMEOUT = float(os.environ.get("PEXAFY_IMG_FETCH_TIMEOUT", "15"))
_MAX_IMG_BYTES = int(os.environ.get("PEXAFY_IMG_MAX_BYTES", str(20 * 1024 * 1024)))
_IMAGE_TOOL_DESCRIPTION = (
    "Find visually similar stock photos from an EXAMPLE IMAGE, optionally TWEAKED with "
    "words. This is the right tool for 'find photos LIKE THIS but <change>' (e.g. 'like "
    "this but with their hands raised', 'the same scene but at night'). "
    "Give the reference image one of three ways: (1) `image_url` — a public http(s) link: a "
    "photo from a PREVIOUS search result (reuse its `image_url`/`urls.regular`), or any "
    "public URL the user provides; (2) `image_file` — auto-filled by the host when the user "
    "UPLOADS an image (e.g. ChatGPT) — it is populated by the host, not by the caller; "
    "(3) `image_base64` — raw base64 image bytes, for a programmatic client that already "
    "holds the file. A chat assistant has no access to the exact bytes of an image it was "
    "shown, so `image_base64` is not available to it. Put any change in `q`; "
    "raise `text_alpha` to weight the text more. "
    "If the reference image has no URL and the host did not auto-provide `image_file` (e.g. a "
    "file pasted into a chat that can't be forwarded), you cannot send it — describe what you "
    "see and use search_photos instead. Every result carries an `attribution` you show."
)


# The schema `image_file` must carry, fixed by OpenAI's Apps SDK: a host that
# uploads a file into a tool call fills these fields in, and their app-directory
# scan refuses a server whose declaration differs. All four properties must be
# declared even though two are optional, exactly `download_url` and `file_id` are
# required, and no other field may appear.
#
# Written out rather than inferred: `dict | None` yields
# `{"anyOf": [{"type": "object", "additionalProperties": true}, {"type": "null"}]}`,
# which declares no properties at all, and even a Pydantic model would sit behind
# the same `anyOf` wrapper. The parameter stays optional by being absent from the
# tool's `required` list — an optional property, not a nullable one.
FILE_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    # A sibling of `type`, not a fifth property: their contract constrains what the
    # file object contains, and every other parameter of every other tool carries
    # one. Kept in the literal so the test below pins the description too.
    "description": (
        "Filled in by the host when the user uploads an image, not by the caller. "
        "Carries the upload's `download_url` and `file_id`."
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


async def _fetch_image(url: str) -> tuple[bytes, str, str]:
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        raise ToolError("Please provide a direct http(s) URL to an image (JPEG, PNG, WebP or AVIF).")
    try:
        async with httpx.AsyncClient(timeout=_IMG_FETCH_TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url.strip(), headers={"User-Agent": "PexafyMCP/1.0"})
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not download that image URL: {exc}") from exc
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        raise ToolError(f"That URL did not return an image (got '{ctype or 'unknown'}').")
    data = r.content
    if len(data) > _MAX_IMG_BYTES:
        raise ToolError("That image is too large (max 20 MB).")
    name = (url.rsplit("/", 1)[-1].split("?")[0] or "image").strip() or "image"
    return data, ctype, name


_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
    (b"RIFF", "image/webp"),
)


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
        raise ToolError("That image is too large (max 20 MB).")
    ctype = next((mime for magic, mime in _IMAGE_MAGIC if data.startswith(magic)),
                 "application/octet-stream")
    return data, ctype, "upload"


async def search_photos_by_image(
    image_url: str | None = None,
    image_file: dict | None = None,
    image_base64: str | None = None,
    q: str | None = None,
    orientation: str | None = None,
    source: str | None = None,
    color_name: str | None = None,
    license_type: str | None = None,
    photographer: str | None = None,
    after_date: str | None = None,
    text_alpha: float | None = None,
    cursor: str | None = None,
) -> ToolResult:
    # Resolve the reference image from any of the three accepted forms:
    #   image_base64 — raw bytes a programmatic client already holds (decoded here);
    #   image_file   — auto-injected by ChatGPT for an upload (fetch its download_url);
    #   image_url    — a public URL (a previous result's url, or one the user gives).
    if image_base64:
        data, ctype, name = _decode_base64_image(image_base64)
    else:
        url = ""
        if isinstance(image_file, dict):
            url = image_file.get("download_url") or image_file.get("url") or ""
        url = url or (image_url or "")
        if not url:
            raise ToolError(
                "Provide `image_url` (a public URL), or `image_file`/`image_base64` if "
                "your client supplies the uploaded file."
            )
        data, ctype, name = await _fetch_image(url)
    params: dict[str, object] = {}
    for key, value in (
        ("q", q), ("orientation", orientation), ("source", source),
        ("color_name", color_name), ("license_type", license_type), ("photographer", photographer),
        ("after_date", after_date), ("text_alpha", text_alpha), ("cursor", cursor),
    ):
        if value is not None:
            params[key] = value
    try:
        resp = await client.post(
            "/api/v1/search/photos", files={"image": (name, data, ctype)}, params=params
        )
        resp.raise_for_status()
    except ToolError:
        raise  # plan-limit messages from _surface_plan_limits — pass through
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = ((exc.response.json() or {}).get("error") or {}).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        raise ToolError(f"Pexafy could not search by that image. {detail}".strip()) from exc
    return ToolResult(structured_content=resp.json())


# Every tool here reads: it searches a catalogue and returns results. Saying so in
# the tool's annotations is not decoration — a host uses `readOnlyHint` to decide
# whether a call needs the user's confirmation, and Anthropic's connector
# directory rejects a submission whose tools carry no read-only/destructive hint.
# `openWorldHint` because results come from a live catalogue that changes; the
# same query tomorrow may return different photographs.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


# Two of the three ways in to the by-image tool (`image_file` carries its own, in
# FILE_PARAM_SCHEMA). They exist nowhere in the spec — the
# generated tool they replace took its image as a multipart upload — so their
# wording is written here. Every other parameter takes the API's own words.
_IMAGE_INPUT_DESCRIPTIONS: dict[str, str] = {
    "image_url": (
        "Public http(s) URL of the reference image. Reuse the `image_url` of a photo "
        "from a previous search result, or any public URL the user provides."
    ),
    "image_base64": (
        "The reference image as base64 bytes, optionally as a `data:` URL. For a client "
        "that already holds the bytes; prefer `image_url` when a link exists."
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


def _describe_image_tool_params(tool, from_spec: dict[str, str]) -> None:
    """Give every parameter of the by-image tool a description.

    It is the one tool written by hand here, so its parameters carry what a Python
    signature carries: nothing. A model then has to guess what `text_alpha` or
    `after_date` mean on this tool while the same parameter is documented on
    `search_photos` — and a directory scoring tool quality marks the gap (Smithery:
    "Parameter descriptions 2/3", the only point our listing was missing).

    The filters take their wording from the spec operation this tool posts to,
    after tooling.customize_spec has tuned it, so the two cannot drift; only the
    three image inputs are written above.
    """
    for name, schema in tool.parameters.get("properties", {}).items():
        if not isinstance(schema, dict) or schema.get("description"):
            continue
        description = _IMAGE_INPUT_DESCRIPTIONS.get(name) or from_spec.get(name)
        if description:
            schema["description"] = description


def _generated_tool_customizer(borrowed: dict):
    """Hook run on every tool FastMCP generates from the spec.

    It stamps the read-only annotations, and keeps `search_photos`' output schema
    in `borrowed` for the hand-written by-image tool, which posts to the same
    endpoint and returns the same envelope. Captured here because it is the only
    synchronous sight of a generated tool — everything the server exposes
    afterwards is behind an async accessor.
    """

    def customize(route, component) -> None:
        if not isinstance(component, OpenAPITool):
            return
        component.annotations = READ_ONLY.model_copy(
            update={"title": TOOL_TITLES.get(component.name)}
        )
        if component.name == "search_photos" and component.output_schema:
            borrowed["search_result"] = component.output_schema

    return customize


def build_server() -> FastMCP:
    """Assemble the MCP server: load the vendored OpenAPI snapshot, tune it for an
    LLM, then wire the search tools, the custom image-URL tool, the inline-grid MCP
    App resource and /health. Importing this module has no side effects; all the
    construction (and any opt-in network) happens here, when the server is built.
    """
    spec = _load_openapi_spec()
    tooling.customize_spec(spec, _load_facets())
    # Filled by the hook below, while the generated tools are being built.
    borrowed_schemas: dict[str, dict] = {}

    # Clean tool names from the verbose FastAPI operationIds (no hardcoded list).
    mcp_names = {
        op["operationId"]: re.split(r"_api(_v1)?_", op["operationId"])[0]
        for methods in spec["paths"].values()
        for op in methods.values()
        if "operationId" in op
    }
    # The derived name would be `photo_similar` — a noun, where a tool name should
    # read as the action it performs. Both directories say so, and OpenAI's own
    # example is `get_order_status`. Overridden here rather than renamed in the
    # API's operationId, so regenerating the vendored spec cannot silently undo it.
    mcp_names.update(TOOL_NAME_OVERRIDES)

    # OAuth Resource Server (only when enabled): validates Bearer tokens (resolving
    # them to the user's Pexafy API key, see auth.py) and exposes RFC 9728 metadata.
    auth_provider = (
        RootAliasedAuthProvider(
            token_verifier=PexafyResolveVerifier(OAUTH_RESOLVE_URL, OAUTH_RESOLVE_SECRET),
            authorization_servers=[OAUTH_AS_URL],
            base_url=MCP_PUBLIC_URL,
            scopes_supported=["read"],
            resource_name="Pexafy MCP",
        )
        if OAUTH_ENABLED
        else None
    )

    # The surface is narrowed to the search core; everything else is excluded —
    # /usage (numbers surface via limit messages), /facets (values hardcoded into the
    # docs), /collections, get_photo, and the POST image-upload tool (replaced by the
    # URL-based search_photos_by_image registered below).
    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=client,
        name="pexafy",
        mcp_names=mcp_names,
        mcp_component_fn=_generated_tool_customizer(borrowed_schemas),
        auth=auth_provider,
        route_maps=[
            RouteMap(pattern=r"^/api/v1/usage", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/facets", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/collections", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/popular-searches", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/api/v1/photos/[^/]+$", mcp_type=MCPType.EXCLUDE),
            RouteMap(methods=["POST"], pattern=r"^/api/v1/search/photos$", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r".*", mcp_type=MCPType.TOOL),
        ],
    )

    # Plain-language titles, the inline-grid link (when previews are configured), and
    # the OpenAPI Apps SDK file-param hint so ChatGPT auto-injects an uploaded image
    # into `image_file` on search_photos_by_image.
    def _meta_for(name: str) -> dict | None:
        meta: dict = {}
        if previews.PREVIEWS_AVAILABLE:
            meta["ui"] = {"resourceUri": widget.GRID_URI}
        if name == "search_photos_by_image":
            meta["openai/fileParams"] = ["image_file"]
        return meta or None

    mcp.add_transform(ToolTransform({
        name: ToolTransformConfig(title=title, meta=_meta_for(name)) if _meta_for(name)
        else ToolTransformConfig(title=title)
        for name, title in TOOL_TITLES.items()
    }))

    # Custom URL image-search tool (replaces the excluded multipart POST tool).
    # Built rather than decorated so the schema can be corrected before the tool is
    # registered: `image_file: dict | None` infers a schema the Apps SDK rejects,
    # and it is replaced here by the one it fixes (see FILE_PARAM_SCHEMA). The
    # signature stays `dict | None` — what arrives at runtime is a plain dict, and
    # the tool reads it defensively.
    #
    # Its results are shaped by the API, not by this function, so the return type
    # is `ToolResult` and nothing describes the payload — the tools generated from
    # the spec get an output schema, this one had none, and ChatGPT shows the gap
    # to the user as a badge on the tool. The schema is BORROWED from
    # `search_photos` rather than written out again: both tools post to the same
    # endpoint and return the same envelope, so a copy here would be a second
    # source of truth, free to drift from the spec every tool else follows.
    image_tool = FunctionTool.from_function(
        search_photos_by_image,
        name="search_photos_by_image",
        description=_IMAGE_TOOL_DESCRIPTION,
        annotations=READ_ONLY.model_copy(
            update={"title": TOOL_TITLES["search_photos_by_image"]}
        ),
        output_schema=borrowed_schemas.get("search_result"),
    )
    image_tool.parameters["properties"]["image_file"] = FILE_PARAM_SCHEMA
    # Same endpoint, same filters, same words: the POST operation is excluded from
    # tool generation (this tool replaces it) but it still documents the parameters.
    _describe_image_tool_params(
        image_tool, _spec_param_descriptions(spec, "/api/v1/search/photos", "post")
    )
    mcp.add_tool(image_tool)

    # Inline result-grid MCP App resource — only when the thumbnail CDN is configured.
    if previews.PREVIEWS_AVAILABLE:
        resource_domains = [previews.THUMB_ORIGIN, *widget.RESOURCE_EXTRA_DOMAINS]
        connect_domains = widget.RESOURCE_EXTRA_DOMAINS or None
        mcp.resource(
            widget.GRID_URI,
            name="pexafy_results_grid",
            title="Pexafy results grid",
            mime_type=UI_MIME_TYPE,
            app=AppConfig(
                csp=ResourceCSP(
                    resource_domains=resource_domains,
                    connect_domains=connect_domains,
                ),
                domain=WIDGET_DOMAIN,
            ),
            # The same two facts under the names ChatGPT reads. FastMCP writes the
            # spec form (`_meta.ui.*`); ChatGPT's compatibility aliases are written
            # here, from the SAME values, so the two encodings cannot drift.
            # Without the domain its portal refuses the submission ("a unique
            # domain is required"), and the CSP alias is the one that decides
            # whether thumbnails load once the app is published rather than
            # previewed — a failure that cannot be tested before publishing.
            meta={
                "openai/widgetDomain": WIDGET_DOMAIN,
                "openai/widgetCSP": {
                    "resource_domains": resource_domains,
                    "connect_domains": connect_domains or [],
                },
            },
        )(_grid_html)
        logger.info("Inline result-grid MCP App ON — %s (SDK inlined=%s, thumbs %s)",
                    widget.GRID_URI, widget.SDK_INLINED, previews.THUMB_ORIGIN)

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
    # `initialize` and `tools/list` need a token — correctly so, and Smithery's own
    # guidance is that an OAuth server SHOULD answer 401 so the flow is discovered —
    # but that leaves a scanner with nothing to show: our Smithery listing sat empty,
    # its scan recorded as AUTH_TIMEOUT, and their bot had asked for this very path
    # (404 at the time). Smithery documents it as the manual escape hatch when
    # "automatic scanning can't complete (auth wall, ...)", and the same document is
    # what the pre-connect discovery draft (SEP-2127) and a growing crowd of
    # directory probes look for.
    #
    # Generated from the live server, never hand-written: the tools carry their own
    # wire-format schemas, so the card cannot drift from what `tools/list` returns.
    # Public on purpose — it is the tool catalogue every client receives on connect,
    # and it names no user and no credential.
    @mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
    async def server_card(_request: Request) -> JSONResponse:
        tools = [
            tool.to_mcp_tool().model_dump(mode="json", exclude_none=True)
            for tool in await mcp.list_tools()
        ]
        resources = [
            resource.to_mcp_resource().model_dump(mode="json", exclude_none=True)
            for resource in await mcp.list_resources()
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
                # can discover or be issued.
                "authentication": {
                    "required": OAUTH_ENABLED,
                    "schemes": ["oauth2"] if OAUTH_ENABLED else [],
                },
                "tools": tools,
                "resources": resources,
                "prompts": [],
            }
        )

    # Answer the sessionless 2026-07-28 discovery probe before the SDK builds a
    # transport it will never be able to reach again. See sessions.py — the reply
    # is the one the SDK already sends, so no client behaviour changes.
    sessions.install()

    # Prometheus scrape (no auth, and blocked at the edge — Caddy 404s /metrics on
    # mcp.pexafy.com, so only the monitoring stack on the Docker network reads it).
    # One number matters here: how many Streamable-HTTP sessions the process is
    # holding. The SDK creates one per client connection and only drops it when the
    # client says goodbye, which almost none of them do — so this curve is the one
    # that decides, later, whether an idle timeout is worth the reconnections it
    # would cost. `/health` stays a liveness probe; this is the gauge.
    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(request: Request) -> PlainTextResponse:
        if METRICS_TOKEN:
            offered = request.headers.get("Authorization", "")
            scheme, _, credential = offered.partition(" ")
            # compare_digest, not ==: a token is a secret, and the timing of a
            # string comparison tells an attacker how much of it they guessed.
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                credential.strip(), METRICS_TOKEN
            ):
                return PlainTextResponse("Unauthorized", status_code=401)
        lines = []
        open_count = sessions.open_sessions()
        if open_count is None:
            # sessions.py has already logged why. Emit nothing rather than a zero
            # that Grafana would draw as "all is well".
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

    # Liveness/readiness probe (no auth) — reports the live tool count.
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        try:
            tool_count = len(await mcp.list_tools())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Health check could not list tools: %s", exc)
            return JSONResponse({"status": "degraded", "error": str(exc)}, status_code=503)
        return JSONResponse(
            {"status": "ok", "tools": tool_count, "oauth": OAUTH_ENABLED, "transport": TRANSPORT}
        )

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


def main() -> None:
    """Console-script entry point (`pexafy-mcp`).

    Transport is configurable: "stdio" (default, for Claude Desktop/Code) or
    "http" (Streamable HTTP, for a remote service). Host/port via env.
    """
    import sys

    from . import __version__

    args = sys.argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(_USAGE)
        return
    if any(a in ("-V", "--version") for a in args):
        print(f"pexafy-mcp {__version__}")
        return

    mcp = build_server()
    if TRANSPORT == "http":
        mcp.run(transport="http", host=MCP_HOST, port=MCP_PORT, middleware=HTTP_MIDDLEWARE)
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
