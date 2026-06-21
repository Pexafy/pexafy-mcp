"""Signed thumbnail URLs for the search results — fuel for the inline UI grid.

Earlier this module embedded base64 ``ImageContent`` blocks in the tool result.
That was dropped: claude.ai never rendered those inline (only inside a collapsed
tool panel), and shipping ~10 base64 images per search added heavy, useless tokens
to the model's context. The inline result grid is now drawn by an MCP App widget
(see widget.py) that runs in the user's browser.

What the widget needs is a loadable thumbnail URL per photo. So instead of fetching
images server-side, we just SIGN a short-lived Pexafy thumbnail URL and inject it
into each photo of the tool's ``structuredContent`` as ``preview_url``. The HMAC
secret stays server-side (never reaches the iframe); the widget only ever sees the
already-signed URL.

Engages only when PEXAFY_THUMB_BASE_URL + PEXAFY_THUMB_HMAC_SECRET are set (matching
the Pexafy thumb proxy); otherwise photos simply carry no ``preview_url`` and the
widget falls back to the public ``urls.*`` values.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

logger = logging.getLogger("pexafy.mcp")

PREVIEWS_ENABLED = os.environ.get("PEXAFY_MCP_PREVIEWS", "1") == "1"

THUMB_WIDTH = int(os.environ.get("PEXAFY_THUMB_WIDTH", "480"))
THUMB_BASE_URL = os.environ.get("PEXAFY_THUMB_BASE_URL", "").rstrip("/")
THUMB_HMAC_SECRET = os.environ.get("PEXAFY_THUMB_HMAC_SECRET", "")
THUMB_TTL = int(os.environ.get("PEXAFY_THUMB_URL_TTL", str(4 * 3600)))

# We can produce preview URLs only when we can sign Pexafy thumbnail URLs.
PREVIEWS_AVAILABLE = bool(PREVIEWS_ENABLED and THUMB_BASE_URL and THUMB_HMAC_SECRET)

# Origin the grid widget loads thumbnails from — declared in the resource CSP.
THUMB_ORIGIN = THUMB_BASE_URL


def sign_thumb_url(photo_id: str, width: int | None = None) -> str:
    """Short-lived signed thumbnail URL — mirrors the Pexafy thumb-proxy signing scheme."""
    w = width or THUMB_WIDTH
    exp = int(time.time()) + THUMB_TTL
    sig = hmac.new(
        THUMB_HMAC_SECRET.encode(), f"{photo_id}:{w}:{exp}".encode(), hashlib.sha256
    ).hexdigest()[:16]
    return f"{THUMB_BASE_URL}/{photo_id}/{w}w.jpg?e={exp}&s={sig}"


def inject_preview_urls(data) -> None:
    """Add a signed ``preview_url`` to each photo in a decoded API response (in place).

    Accepts the parsed JSON body of a search response (``{"data": [photos], …}``)
    or a bare list of photos. No-op when previews aren't configured.
    """
    if not PREVIEWS_AVAILABLE:
        return
    if isinstance(data, dict):
        photos = data.get("data")
    elif isinstance(data, list):
        photos = data
    else:
        return
    if not isinstance(photos, list):
        return
    n = 0
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        pid = photo.get("photo_id")
        if pid and "preview_url" not in photo:
            photo["preview_url"] = sign_thumb_url(str(pid))
            n += 1
    if n:
        logger.info("Injected %d preview_url(s)", n)
