"""How the grid can ask the same question again, with one thing changed.

The problem this exists for
---------------------------
The grid draws the photographs of one answer and knows nothing about the question that
produced them. Two of its controls need that question. The ≈ button asks for more like
one tile: that tile's `photo_id`, with the words the page was found under, without
which each refinement drifts off its subject. The shape filter re-asks the same
question with a shape attached: filtering a page of sixteen results down to the four
portraits in it is not what "show me portraits" means, and the widget cannot ask for
sixteen portraits without knowing what was searched for.

Nothing in the payload carries that. `data` is photographs, and the arguments of the call
are not part of it — deliberately, because the model does not need to be told what it
just asked.

So the question rides on `_meta`, the half of the answer the host hands to the frame and
the model never reads (see previews.py for the same reasoning about image links). The
frame gets what it needs to re-ask; the model's context does not grow by a byte.

The link to this answer on the website travels the same way, for the same reason
(CTA_META_KEY): the grid draws it — the "Open in Pexafy" button, the wordmark — and reads
from its origin which site the server belongs to, preprod included. It used to ride in
`structuredContent`, where it was one more link a model was handed and had no use for.
So does the account corner (budget.ACCOUNT_META_KEY): who is asking, the way to sign in,
and the wording of the allowance panel, worked out by the HTTP hooks and attached here.

What travels, and what does not
-------------------------------
Only arguments a frame could legitimately send itself, named per tool (REPLAYABLE_ARGS):

  search_photos            `english_search_sentence`
  search_photos_by_image   `photo_id`, a photograph of the catalogue, with the
                           `english_search_sentence` it was found under — the words keep
                           a refinement on its subject, so a shape asked for on a
                           refined page carries them; or `image_url`, a LINK. Never an
                           uploaded file: it arrives as host-managed bytes that cannot be
                           replayed from an iframe, and echoing its identifier back would
                           say what somebody uploaded to anyone who can read the frame's
                           meta. An upload, even with words, has no origin at all.

plus the shape filter, whatever its state, so the button can open showing what is
actually applied rather than what the widget last remembered pressing.

Everything else is dropped by omission: the allow-list names what MAY travel, so a
parameter added to a tool later does not start leaking because nobody thought about it
here.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote_plus

from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import budget, tooling

logger = logging.getLogger("pexafy.mcp")

# The key the widget reads. Namespaced like the others on this channel.
ORIGIN_META_KEY = "pexafy/origin"

# The link to the rest of the answer on the website, under the key the widget reads.
CTA_META_KEY = "pexafy/cta"

# The site this server belongs to — the variable budget.py reads for its links too, so a
# preprod server never sends a reader to production.
WEB_URL = os.environ.get("PEXAFY_WEB_URL", "https://pexafy.com").rstrip("/")

# Per tool, the arguments that may be echoed back to the frame. The shape filter is
# added to every one of them below, under the name the tool surface uses.
REPLAYABLE_ARGS: dict[str, frozenset[str]] = {
    tooling.PUBLIC_TOOL_NAMES["search_photos"]: frozenset({tooling.PUBLIC_QUERY_PARAM}),
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: frozenset(
        {"image_url", "photo_id", tooling.PUBLIC_QUERY_PARAM}
    ),
}

# Per tool, the arguments of which one must be kept for a replay to work: the by-image
# tool refuses a call with words and no reference.
REQUIRED_ARGS: dict[str, frozenset[str]] = {
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: frozenset({"image_url", "photo_id"}),
}

# The tools whose `_meta` carries the account corner (budget.ACCOUNT_META_KEY): the two
# searches, whose answers the grid draws, and the connect tool, whose probe it reads.
# The file tool reaches the API too, and the hook learns who is asking there as well;
# no grid draws that answer, so nothing is attached to it.
ACCOUNT_TOOLS = frozenset({
    tooling.PUBLIC_TOOL_NAMES["search_photos"],
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
    tooling.PUBLIC_TOOL_NAMES["connect_account"],
})


def origin_for(tool: str, arguments: dict | None) -> dict | None:
    """The replayable form of one call, or None when this tool has none.

    A missing argument is left out rather than sent as null: the frame builds its next
    call by spreading this dict, and a null sentence would travel as an explicit empty
    search instead of no search at all.
    """
    allowed = REPLAYABLE_ARGS.get(tool)
    if allowed is None:
        return None
    args = arguments if isinstance(arguments, dict) else {}
    kept = {
        key: value for key, value in args.items()
        if key in allowed and value not in (None, "", [])
    }
    required = REQUIRED_ARGS.get(tool)
    if not kept or (required and not required.intersection(kept)):
        # A by-image search run from an uploaded file has nothing replayable in it,
        # even with words beside it — and saying so is the point: the frame reads the
        # absence and leaves its shape button out rather than drawing one that cannot
        # work.
        return None
    shape = args.get(tooling.PUBLIC_ORIENTATION_PARAM) or args.get(
        tooling.API_ORIENTATION_PARAM)
    if isinstance(shape, str):
        shape = [shape]
    return {
        "tool": tool,
        "arguments": kept,
        # The filter the answer on screen was produced under. A list, always, including
        # when it is empty: the frame tests its length, and `null` vs `[]` vs absent is
        # three ways to say one thing.
        "orientation": [str(s) for s in shape] if isinstance(shape, list) else [],
    }


def cta_for(tool: str, arguments: dict | None) -> dict | None:
    """The link to this answer on the Pexafy website, or None:

      - a text search            → 'Open in Pexafy', the live results page (/?q=…)
      - a `photo_id` reference   → 'Open in Pexafy', that photo's page, which
                                   lists its neighbours
      - an image as reference    → 'Image search at Pexafy', the home page: no public URL
                                   mirrors an uploaded image
      - any other tool           → None

    The label says where the link goes and nothing about the grid. It said "Full
    results", as if the grid held back the rest of an answer, and OpenAI's guidelines
    ask a plugin not to present itself as a lesser version of the product. Built from
    the call's own arguments, as the tool surface names them — the same words the API
    was sent.
    """
    args = arguments if isinstance(arguments, dict) else {}
    if tool == tooling.PUBLIC_TOOL_NAMES["search_photos"]:
        words = str(args.get(tooling.PUBLIC_QUERY_PARAM) or "").strip()
        if words:
            return {"label": "Open in Pexafy", "url": f"{WEB_URL}/?q={quote_plus(words)}"}
        return {"label": "Open in Pexafy", "url": WEB_URL}
    if tool == tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]:
        ref = str(args.get("photo_id") or "").strip()
        if ref:
            return {"label": "Open in Pexafy", "url": f"{WEB_URL}/photos/{ref}/"}
        return {"label": "Image search at Pexafy", "url": WEB_URL}
    return None


class AttachOrigin(Middleware):
    """Put the replayable form of the call, the link to it on the site and the account
    corner on the result's `_meta`.

    Separate from SplitPreviews rather than folded into it: that middleware returns
    early when an answer carries no image links, and a grid that came back empty — no
    photographs of this shape — is exactly the answer whose origin the frame needs most,
    since the way out of an empty grid is to re-ask without the filter. The same goes
    for the account corner: the wall is an answer with no photographs.

    The account corner is collected while the call runs (budget.collect_for_grid), from
    the API's answer. A result that already carries one — the wall that
    linking.OfferAccountLink builds from a refusal — keeps its own.

    `_meta` is merged, never replaced.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        with budget.collect_for_grid() as grid:
            result = await call_next(context)
        message = getattr(context, "message", None)
        tool = getattr(message, "name", "") or ""
        arguments = getattr(message, "arguments", None)
        extra = {}
        origin = origin_for(tool, arguments)
        if origin:
            extra[ORIGIN_META_KEY] = origin
        cta = cta_for(tool, arguments)
        if cta:
            extra[CTA_META_KEY] = cta
        if (grid and tool in ACCOUNT_TOOLS
                and budget.ACCOUNT_META_KEY not in (getattr(result, "meta", None) or {})):
            extra[budget.ACCOUNT_META_KEY] = dict(grid)
        if not extra:
            return result
        meta = dict(getattr(result, "meta", None) or {})
        meta.update(extra)
        try:
            result.meta = meta
        except Exception:  # noqa: BLE001 — a frozen result simply has no shape button
            logger.warning("Could not attach %s to the result meta", ", ".join(extra))
        return result
