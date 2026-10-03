"""Signed thumbnail URLs for the search results — what the inline grid renders.

The widget needs one loadable thumbnail URL per photo. Rather than fetching images
server-side, this module signs a Pexafy thumbnail URL and injects it into each photo of
the tool's ``structuredContent`` as ``preview_url``. The HMAC secret stays server-side
and never reaches the iframe; the widget only ever sees an already-signed URL.

Both URLs are built to outlive the search. A tool result is not a page view: it stays
in the conversation, and the reader scrolls back to it days later. The viewer's file
uses the proxy's no-expiry form (PERMANENT_WIDTH); the grid's 480w file, which that
form does not cover, carries the longest expiry instead.

The images themselves are not sent as base64 ``ImageContent`` blocks: hosts render
those in a collapsed tool panel rather than inline, and a dozen of them per search is a
large, useless addition to the model's context.

Engages only when PEXAFY_THUMB_BASE_URL and PEXAFY_THUMB_HMAC_SECRET are both set and
PEXAFY_MCP_PREVIEWS is not off. Otherwise photos carry no ``preview_url`` and the server
offers no grid at all: the widget draws only from this signed CDN, the one image origin
its CSP allows (see server.build_server).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import coach, hosts, selection, tooling
from .env import flag

logger = logging.getLogger("pexafy.mcp")

PREVIEWS_ENABLED = flag("PEXAFY_MCP_PREVIEWS", True)

THUMB_WIDTH = int(os.environ.get("PEXAFY_THUMB_WIDTH", "480"))

# The one width the thumb proxy signs WITHOUT an expiry. Must match PERMANENT_WIDTH in
# pexafy/img-proxy/main.py, pexafy/cloudflare/thumb-worker/src/worker.js and
# django_app/core/utils/thumb.py — the proxy answers 403 for a no-expiry URL at any
# other width. This is the file the viewer shows: one photo filling the surface, so it
# deserves the sharp copy, and its link never dies.
PERMANENT_WIDTH = 1280

THUMB_BASE_URL = os.environ.get("PEXAFY_THUMB_BASE_URL", "").rstrip("/")
THUMB_HMAC_SECRET = os.environ.get("PEXAFY_THUMB_HMAC_SECRET", "")

# How long a GRID thumbnail stays loadable: 30 days by default, because a tool result
# stays in the conversation as long as the thread does, and past the expiry the proxy
# answers 410 ("Preview unavailable" on every card). The grid cannot use the permanent
# form — that is PERMANENT_WIDTH only, and a 1280w file behind a 140px tile is about
# seven times the bytes of the 480w one.
THUMB_TTL = int(os.environ.get("PEXAFY_THUMB_URL_TTL", str(30 * 24 * 3600)))
# …rounded UP to a fixed step, so the same photo yields the SAME URL for everyone
# searching on the same day. An expiry computed from `now` would make a new URL on
# every call and defeat the browser cache.
THUMB_TTL_STEP = int(os.environ.get("PEXAFY_THUMB_URL_STEP", str(24 * 3600)))

# Preview URLs exist only when a Pexafy thumbnail URL can be signed.
PREVIEWS_AVAILABLE = bool(PREVIEWS_ENABLED and THUMB_BASE_URL and THUMB_HMAC_SECRET)

# Origin the grid widget loads thumbnails from — declared in the resource CSP.
THUMB_ORIGIN = THUMB_BASE_URL


def _step_expiry(now: int | None = None) -> int:
    """Expiry at least THUMB_TTL away, rounded up to the next THUMB_TTL_STEP."""
    now = int(time.time()) if now is None else now
    step = max(1, THUMB_TTL_STEP)
    return ((now + THUMB_TTL) // step + 1) * step


def sign_thumb_url(photo_id: str, width: int | None = None) -> str:
    """Signed, expiring thumbnail URL — mirrors the Pexafy thumb-proxy signing scheme."""
    w = width or THUMB_WIDTH
    exp = _step_expiry()
    sig = hmac.new(
        THUMB_HMAC_SECRET.encode(), f"{photo_id}:{w}:{exp}".encode(), hashlib.sha256
    ).hexdigest()[:16]
    return f"{THUMB_BASE_URL}/{photo_id}/{w}w.jpg?e={exp}&s={sig}"


def sign_thumb_url_permanent(photo_id: str) -> str:
    """Non-expiring thumbnail URL — the same HMAC, minus the expiry.

    Only PERMANENT_WIDTH is accepted in this form (the proxy and the edge worker both
    refuse any other width without an `?e`), so the width is not a parameter.
    """
    sig = hmac.new(
        THUMB_HMAC_SECRET.encode(),
        f"{photo_id}:{PERMANENT_WIDTH}".encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"{THUMB_BASE_URL}/{photo_id}/{PERMANENT_WIDTH}w.jpg?s={sig}"


def _photos_of(data):
    if isinstance(data, dict):
        return data.get("data")
    if isinstance(data, list):
        return data
    return None


# ── Which channel the grid's image links travel on ───────────────────────────
# They are read by the widget and by nothing else. The model cannot open a URL — it has
# no network — so for it the two signed links are dead weight: 3,936 characters of a
# measured search, 28% of what is left after the curation.
#
# `_meta` on the tool result is the channel for exactly this: OpenAI describes it as
# "client-specific data hidden from the model". The widget gets it, the model does not.
#
#   both     (default)  in BOTH halves; the widget prefers `_meta`. Nothing can break.
#   meta                `_meta` only — the saving, once a host is proven to pass it on.
#   content             structuredContent only. SplitPreviews then adds no `_meta` at
#                       all, the selection capability included.
PREVIEW_CHANNEL = os.environ.get("PEXAFY_PREVIEWS_CHANNEL", "both").strip().lower()
PREVIEW_META_KEY = "pexafy/previews"


def previews_for_meta(data) -> dict | None:
    """The image links, keyed by photo_id, for the `_meta` half of the answer."""
    if PREVIEW_CHANNEL == "content":
        return None
    photos = _photos_of(data)
    if not isinstance(photos, list):
        return None
    out = {}
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        pid = photo.get("photo_id")
        small, large = photo.get("preview_url"), photo.get("preview_url_large")
        if pid and (small or large):
            out[str(pid)] = {"small": small or "", "large": large or ""}
    return out or None


def strip_previews_from_content(data) -> None:
    """Take the links out of what the model reads — only once `meta` is trusted."""
    if PREVIEW_CHANNEL != "meta":
        return
    photos = _photos_of(data)
    if not isinstance(photos, list):
        return
    for photo in photos:
        if isinstance(photo, dict):
            photo.pop("preview_url", None)
            photo.pop("preview_url_large", None)


def inject_ranks(data) -> None:
    """Number each photo of a search response 1..N (in place) as ``rank``.

    The rank is the result's position in this answer: a handle for talking about a
    result ("the second one"), which the model turns back into that photo's
    ``photo_id``. The grid draws no number on a result. A rank the API already set is
    kept. Independent of previews — always runs.
    """
    photos = _photos_of(data)
    if not isinstance(photos, list):
        return
    rank = 0
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        rank += 1
        photo.setdefault("rank", rank)


def inject_preview_urls(data) -> None:
    """Add a signed ``preview_url`` to each photo in a decoded API response (in place).

    Accepts the parsed JSON body of a search response (``{"data": [photos], …}``)
    or a bare list of photos. No-op when previews aren't configured.

    Two widths go in: ``preview_url`` (480w, long-lived) for the grid and
    ``preview_url_large`` (PERMANENT_WIDTH, no expiry at all) for the viewer. Each is
    signed for its own width — the HMAC covers it, so the widget cannot mint a bigger
    file by rewriting the URL it was handed.
    """
    if not PREVIEWS_AVAILABLE:
        return
    photos = _photos_of(data)
    if not isinstance(photos, list):
        return
    n = 0
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        pid = photo.get("photo_id")
        if pid and "preview_url" not in photo:
            photo["preview_url"] = sign_thumb_url(str(pid))
            photo["preview_url_large"] = sign_thumb_url_permanent(str(pid))
            n += 1
    if n:
        logger.info("Injected %d preview_url(s)", n)


class SplitPreviews(Middleware):
    """Put the grid's image links on `_meta`, the channel the model does not read.

    It runs outside the other middlewares that shape a result, so it sees the finished
    answer of either search, whoever built it — the generated text search or the
    hand-written by-image one. An answer without image links (an empty grid, the wall,
    a tool that returns no photographs) is left as it is.

    `_meta` is merged, never replaced: a result may already carry one.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        if PREVIEW_CHANNEL == "content":
            return result
        structured = getattr(result, "structured_content", None)
        links = previews_for_meta(structured)
        if not links:
            return result
        meta = dict(getattr(result, "meta", None) or {})
        meta[PREVIEW_META_KEY] = links
        # …and the capability that lets the grid this answer draws write back what the
        # reader picks in it. It names this caller and it expires; see selection.py.
        cap = selection.meta_for_result()
        if cap:
            meta[selection.SELECTION_META_KEY] = cap
        # …and whether this reader has met the coach, which plays once per person.
        met = await coach.meta_for_result()
        if met:
            meta[coach.META_KEY] = met
        # …and the assistant's name, which the grid says where the liked photos go.
        assistant = hosts.assistant_name(context)
        if assistant:
            meta[HOST_META_KEY] = {"assistant": assistant}
        try:
            result.meta = meta
        except Exception:  # noqa: BLE001 — a frozen result keeps the links in content
            logger.warning("Could not attach %s to the result meta", PREVIEW_META_KEY)
            return result
        strip_previews_from_content(structured)
        if hosts.renders_grid(context):
            _name_without_links(result, structured, context)
            # VS Code hands the model the structured half too (it read the answer's
            # content.json and listed every `urls.regular`): the grid never reads that
            # link — it draws the signed previews — so it goes from there as well.
            for photo in (structured.get("data") or []) if isinstance(structured, dict) else []:
                if isinstance(photo, dict):
                    photo.pop("urls", None)
                    _previews_without_address(photo)
        return result


# The `_meta` key that says which assistant the person talks to (hosts.assistant_name).
HOST_META_KEY = "pexafy/host"


def _previews_without_address(photo: dict) -> None:
    """The grid's two previews, in the half the model reads too, as a path under
    THUMB_BASE_URL: the grid puts the base back in front (thumbUrl in widget.py), and
    the model has no picture left to show. After `urls` went (2026-10-01), a search
    still handed a Cursor client 32 image links there, two per photograph (measured
    2026-10-02). The whole links stay in `_meta`, which the grid reads first; this copy
    is for a host that does not pass `_meta` on to the grid."""
    for key in ("preview_url", "preview_url_large"):
        link = photo.get(key)
        if not isinstance(link, str):
            continue
        if THUMB_BASE_URL and link.startswith(THUMB_BASE_URL + "/"):
            photo[key] = link[len(THUMB_BASE_URL) + 1:]
        else:
            photo.pop(key, None)


def _name_without_links(result, structured: dict, context) -> None:
    """In a host that shows the grid, the text the model reads names the photographs
    without their image links (grid_summary); `structuredContent` and `_meta`, which the
    grid draws from, are unchanged."""
    args = getattr(getattr(context, "message", None), "arguments", None) or {}
    sentence = args.get(tooling.PUBLIC_QUERY_PARAM) if isinstance(args, dict) else ""
    try:
        from mcp.types import TextContent

        result.content = [TextContent(type="text", text=grid_summary(structured, sentence or ""))]
    except Exception as exc:  # noqa: BLE001 — the full JSON text stays: links, but an answer
        logger.warning("Could not shorten the grid's answer for the model: %s", exc)


def grid_summary(structured: dict, sentence: str = "") -> str:
    """What the model reads of a search answer in a host that shows it as the grid: the
    photographs by `photo_id`, description, credit and licence, without their image
    links. With the links in hand, models put the photographs in the reply again, one
    picture after another under the grid (Cursor, 2026-10-01). A host without the grid
    reads the whole answer, links included, as before."""
    photos = [p for p in (structured.get("data") or []) if isinstance(p, dict)]
    n = len(photos)
    what = f" for “{sentence}”" if sentence else ""
    verb = "is" if n == 1 else "are"
    lines = [
        f"{n} photograph{'' if n == 1 else 's'}{what} {verb} on the person's screen in the "
        "Pexafy grid, with their credits: the person opens, likes and refines them there. "
        "This text names them without their image links, and they need not be shown again. "
        "When the person wants one as a file, to insert, attach or download it, "
        f"{tooling.PUBLIC_TOOL_NAMES['get_photo_file']} returns it with its link."
    ]
    for i, p in enumerate(photos, 1):
        plain = (p.get("attribution") or {}).get("plain") if isinstance(p.get("attribution"), dict) else ""
        credit = (plain or "").split(" (http")[0]
        parts = [str(p.get("photo_id") or "")]
        if p.get("alt_description"):
            parts.append(str(p["alt_description"]))
        if credit:
            parts.append(credit)
        if p.get("license_type"):
            parts.append(f"licence: {p['license_type']}")
        lines.append(f"{i}. " + " — ".join(parts))
    allowance = structured.get("budget")
    if isinstance(allowance, dict) and allowance.get("message"):
        lines.append(str(allowance["message"]))
    if structured.get("notice"):
        lines.append(str(structured["notice"]))
    return "\n".join(lines)
