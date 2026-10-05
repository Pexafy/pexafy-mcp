"""Answer a call made against an older published tool surface.

A directory or a host holds a SNAPSHOT of the tool metadata and routes calls to the live
server. The two move at different speeds: a new snapshot waits for a review, the server
ships when it ships. In between, callers keep sending what the old snapshot declared,
and FastMCP answers an argument the tool no longer declares with `TypeError: Got
unexpected keyword argument(s)` — a failed call, not a degraded one. This middleware
translates the call before it runs:

  - an old tool name becomes the tool that answers it now (RENAMED_TOOLS);
  - a renamed parameter takes its current name: `orientation` on every tool, `q` — and
    `query`, which assistants send in its place — on the two searches (RENAMED_PARAMS,
    RENAMED_PARAMS_PER_TOOL);
  - a retired filter is dropped, and so is a page size, which no snapshot offered
    (RETIRED_PARAMS, RETIRED_PER_TOOL). That costs what the release decided it should
    cost — the filter is not applied — and the search still returns photos.

What the translation does not cover: a tool the host no longer lists. A published ChatGPT
app loses a tool at the next scan of `tools/list`, whatever this module would answer to;
the translation only serves a caller that still SENDS an old name or old parameters.
`get_similar_photos`, the one tool 1.0.0 removed, stayed listed to OpenAI hosts until the
published app's 1.0.0 tools were live, `photo_id` on the by-image search among them. It
is listed nowhere now; a call under its name is translated like any other old name.

Nor does it cover the grid ChatGPT keeps in its cache: for a while after the deploy, the
0.4.12 grid draws 1.0.0's answers. Until PEXAFY_LEGACY_GRID_UNTIL, an OpenAI host's
answer keeps what that grid reads (KeepLegacyGrid).

Every translation is logged: the rate says when this middleware can go. It is the
compatibility half of a release, not a permanent part of the surface.
"""
from __future__ import annotations

import contextvars
import copy
import logging
import math
import os
import time
from datetime import datetime, timezone

from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import hosts, origin, previews, tooling

logger = logging.getLogger("pexafy.mcp")

# The tool 0.4.x offered for "more like this one", gone in 1.0.0: a catalogue photo is
# now `photo_id` on the by-image search, the same argument for the same answer.
SIMILAR_TOOL = "get_similar_photos"

# Renamed, not retired: a shape is a constraint a person states, and it must keep
# working. Global, because every tool that takes a shape takes it under the new name.
# The value is widened on the way: the old surface took a single string, the current
# one takes a list (which is what the API has always accepted).
RENAMED_PARAMS = {tooling.API_ORIENTATION_PARAM: tooling.PUBLIC_ORIENTATION_PARAM}

# The filters 1.0.0 removed — everything tooling.KEEP_PARAMS drops but the mechanical
# parameters, which no published snapshot offered — and the two of those that assistants
# send all the same. `orientation` is absent: it is renamed. Global, because no tool
# takes any of these; a parameter retired on one tool but still live on another goes in
# RETIRED_PER_TOOL instead.
RETIRED_PARAMS = frozenset({
    "color_name",
    "color_hex",
    "color_tolerance",
    "source",
    "license_type",
    "after_date",
    "photographer",
    # Paging went too: an old snapshot may still hand back a `next_cursor` it read
    # from a result and send it as `cursor`. Ignoring it returns the first page —
    # the same photos, not an error.
    "cursor",
    # A page size. No snapshot offered one and the server sets it (server.GRID_PAGE_SIZE),
    # but an assistant that expects one invents it: `per_page` twice and `limit` once in
    # 72 hours of production logs (2026-09-17), each a failed search. Ignored, the call
    # returns the grid's page.
    "per_page",
    "limit",
})


# Retired on one tool only, keyed by that tool's current name. `text_alpha`, the weight
# of the words against the image, is no longer offered on the by-image search: unsent,
# the API applies its own default.
RETIRED_PER_TOOL = {
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: frozenset({"text_alpha"}),
}

# Old tool names, answered by the tool that exists now: `get_similar_photos`, which went
# when `photo_id` became a reference of the by-image search (same argument, same
# answer), and the names older snapshots carried (tooling.LEGACY_TOOL_NAMES). On a host
# still holding such a snapshot, "Unknown tool" would be a failed call, and for the
# similar tool a failed follow-up on the one move the grid invites.
RENAMED_TOOLS = {
    SIMILAR_TOOL: tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
    **tooling.LEGACY_TOOL_NAMES,
}

# `q` is `english_search_sentence` on the two searches, the only tools that ever took it.
# So is `query`, which no snapshot published but assistants send in its place: three
# failed searches in 72 hours of production logs (2026-09-17). `q` comes first, so when
# both are sent the name 0.4.12 published wins. Renamed per tool rather than globally,
# so the rename cannot invent an argument on a tool that has none: the file tool, the
# selection and the account link take no query, and a global entry would hand them one.
# The value is a string on both sides.
QUERY_RENAMES = {
    tooling.API_QUERY_PARAM: tooling.PUBLIC_QUERY_PARAM,
    "query": tooling.PUBLIC_QUERY_PARAM,
}
RENAMED_PARAMS_PER_TOOL = {
    tooling.PUBLIC_TOOL_NAMES["search_photos"]: dict(QUERY_RENAMES),
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: dict(QUERY_RENAMES),
}


class DropRetiredParams(Middleware):
    """Translate an old call before it runs: rename the tool and the renamed
    parameters, drop the retired ones. Changed in place on the message, so the guards
    after this middleware, the tool, and the outer middlewares that read the call once
    it has returned all see the current names."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        old_name = getattr(context.message, "name", "")
        new_name = RENAMED_TOOLS.get(old_name)
        if new_name:
            # The name is read off the message AFTER the middleware chain, so changing
            # it here is what routes the call. Logged: the rate says when this can go.
            context.message.name = new_name
            logger.info("Renamed tool call: %s -> %s", old_name, new_name)
        arguments = getattr(context.message, "arguments", None)
        if isinstance(arguments, dict):
            # Per-tool first: `q` and `query` are renamed only on the two searches, and
            # the tool name has already been translated above, so the lookup is on the
            # current name.
            per_tool = RENAMED_PARAMS_PER_TOOL.get(
                getattr(context.message, "name", ""), {}
            )
            for old_arg, new_arg in per_tool.items():
                if old_arg not in arguments:
                    continue
                value = arguments.pop(old_arg)
                if new_arg in arguments or value is None:
                    continue
                arguments[new_arg] = value
                logger.info(
                    "Renamed parameter on %s: %s -> %s",
                    getattr(context.message, "name", "?"), old_arg, new_arg,
                )
            for old_name, new_name in RENAMED_PARAMS.items():
                if old_name not in arguments:
                    continue
                value = arguments.pop(old_name)
                # An argument already sent under the new name wins: the caller that used
                # it read the current schema, so it is the one that meant what it said.
                if new_name in arguments or value is None:
                    continue
                arguments[new_name] = [value] if isinstance(value, str) else value
                logger.info(
                    "Renamed parameter on %s: %s -> %s",
                    getattr(context.message, "name", "?"), old_name, new_name,
                )
            retired = RETIRED_PARAMS | RETIRED_PER_TOOL.get(
                getattr(context.message, "name", ""), frozenset()
            )
            dropped = sorted(retired.intersection(arguments))
            if dropped:
                for name in dropped:
                    arguments.pop(name, None)
                # Logged, not hidden: the rate of these calls is what says whether
                # hosts are still on the old snapshot, and therefore when this can go.
                logger.info(
                    "Retired parameter(s) ignored on %s: %s",
                    getattr(context.message, "name", "?"),
                    ", ".join(dropped),
                )
        return await call_next(context)


# ── The 0.4.12 grid, still in ChatGPT's cache ─────────────────────────────────
# The grid keeps its URI, `ui://pexafy/grid.html`, from 0.4.12 to 1.0.0, and "ChatGPT may
# continue serving cached resource contents for up to one hour" after a deploy
# (developers.openai.com, Apps SDK, "Other changes"). For up to that hour the published
# app draws 1.0.0's answers with the 0.4.12 grid. Where a grid is drawn, 1.0.0 hands the
# model's half no `urls` and the two previews as a path without address
# (previews.SplitPreviews): 0.4.12's grid reads `structuredContent` alone and puts that
# path in an <img> as it is — no thumbnail, no "Open original image" (study of
# 2026-10-02, C1). OpenAI asks the opposite: "keep … each published UI resource URI
# working during that gap".
#
# So, until PEXAFY_LEGACY_GRID_UNTIL, an OpenAI host's answer keeps in `structuredContent`
# everything that grid reads (bc7b4c8:src/pexafy_mcp/widget.py, `fields()` and
# `render()`): the previews whole, `urls` in every size the API sends, the other fields
# 1.0.0 prunes (LEGACY_GRID_PHOTO_FIELDS) and the link under the grid, `cta`. The text
# the model reads is written from the pruned answer, before anything is put back
# (previews.grid_summary), and keeps no link; `tools/list` does not change. After the
# instant, and for every other host, nothing changes. Decided 2026-10-02: the links in
# full for two hours — set to the time of the deploy plus 2 hours.
LEGACY_GRID_ENV = "PEXAFY_LEGACY_GRID_UNTIL"

# What the 0.4.12 grid reads off a photograph and 1.0.0 takes out: `urls` (the stand-in
# for a missing preview, and "Open original image", `full` first), the photographer's
# link and user name, the dominant colour, the date, and the long description of the
# detail panel. The rest it reads — `photo_id`, `rank`, `preview_url`, `attribution`,
# `photographer_full_name`, `width`, `height`, `source`, `source_image_url`,
# `license_type`, `orientation`, `alt_description` — 1.0.0 still sends.
LEGACY_GRID_PHOTO_FIELDS = (
    "urls",
    "photographer_url",
    "photographer_username",
    "color_name",
    "color_hex",
    "uploaded_on",
    "description",
)


def read_instant(raw: str | None) -> float | None:
    """The instant `raw` names, in epoch seconds, or None for no window: empty or 0.

    Epoch seconds, or ISO 8601 (`2026-10-05T03:00:00Z`; a time with no offset is UTC).
    Anything else raises ValueError: a word, a negative number, or a time no calendar
    holds, epoch milliseconds among them.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if seconds == 0:
        return None
    if seconds < 0 or not math.isfinite(seconds):
        raise ValueError(f"not an instant: {text!r}")
    try:
        datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"not an instant: {text!r}") from exc
    return seconds


def _legacy_grid_until() -> tuple[float | None, str]:
    """(the instant set, or None; the value refused, or "")."""
    raw = os.environ.get(LEGACY_GRID_ENV, "")
    try:
        return read_instant(raw), ""
    except ValueError:
        return None, raw.strip()


# Read at start, like every switch.
LEGACY_GRID_UNTIL, LEGACY_GRID_REFUSED = _legacy_grid_until()


def _utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def legacy_grid_open(now: float | None = None) -> bool:
    """Whether the window is open: an instant is set, and it is still to come."""
    if LEGACY_GRID_UNTIL is None:
        return False
    return (time.time() if now is None else now) < LEGACY_GRID_UNTIL


def keeps_legacy_grid(context=None) -> bool:
    """Whether this caller's answer keeps what the 0.4.12 grid reads: an OpenAI host
    (hosts.is_openai), while the window is open."""
    return legacy_grid_open() and hosts.is_openai(context)


def log_legacy_grid_window() -> None:
    """The line the server writes at start about the window: its state, or a warning
    when the value set is not an instant (and then there is no window)."""
    if LEGACY_GRID_REFUSED:
        logger.warning(
            "%s=%r is not an instant (epoch seconds, or ISO 8601 such as "
            "2026-10-05T03:00:00Z): no window for the 0.4.12 grid",
            LEGACY_GRID_ENV, LEGACY_GRID_REFUSED[:64],
        )
    elif LEGACY_GRID_UNTIL is None:
        logger.info("0.4.12 grid window: off (%s unset)", LEGACY_GRID_ENV)
    elif legacy_grid_open():
        logger.info("0.4.12 grid window: open until %s — an OpenAI host's answers keep the "
                    "links and fields the 0.4.12 grid reads", _utc(LEGACY_GRID_UNTIL))
    else:
        logger.info("0.4.12 grid window: closed since %s", _utc(LEGACY_GRID_UNTIL))


# What the API sent of each photograph, set aside in the HTTP response hook
# (server._enrich_response) before the pruning takes it out, for the middleware below to
# put back. A ContextVar connects the two, as budget._grid_half does; it holds a dict the
# middleware opened, so that a hook run in a copied context still writes where the
# middleware reads.
_legacy_held: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "pexafy_legacy_grid", default=None)


def set_aside_for_legacy_grid(photo) -> None:
    """Keep a copy of what the 0.4.12 grid reads of one photograph, before the pruning
    takes it out — only in a call KeepLegacyGrid opened; nothing otherwise.

    The text a third party wrote is cleaned as 1.0.0 cleans the rest of the photograph
    (tooling.clean_free_text): the description, and the user name, which also loses the
    placeholder words and the bare numbers that name nobody (tooling.clean_photographer).
    """
    held = _legacy_held.get()
    if held is None or not isinstance(photo, dict) or not photo.get("photo_id"):
        return
    kept = {name: copy.deepcopy(photo[name]) for name in LEGACY_GRID_PHOTO_FIELDS
            if name in photo}
    if isinstance(kept.get("description"), str):
        kept["description"] = tooling.clean_free_text(kept["description"])
    if isinstance(kept.get("photographer_username"), str):
        kept["photographer_username"] = tooling.clean_free_text(
            tooling.clean_photographer(kept["photographer_username"]))
    held[str(photo["photo_id"])] = kept


def _restore_legacy_grid(result, held: dict) -> bool:
    """Put back, in the answer's `structuredContent`, what the 0.4.12 grid reads. True
    when the answer has photographs to put it on.

    The fields set aside, by `photo_id`; the previews whole, from `_meta` (the grid's own
    copy, previews.PREVIEW_META_KEY) — where they are absent, no channel took them out of
    `structuredContent` and they are whole there already; the link under the grid, from
    `_meta` too (origin.CTA_META_KEY), under the name 0.4.12 gave it.
    """
    structured = getattr(result, "structured_content", None)
    photos = structured.get("data") if isinstance(structured, dict) else None
    if not isinstance(photos, list) or not photos:
        return False
    meta = getattr(result, "meta", None) or {}
    links = meta.get(previews.PREVIEW_META_KEY)
    links = links if isinstance(links, dict) else {}
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        pid = str(photo.get("photo_id") or "")
        photo.update(held.get(pid) or {})
        link = links.get(pid)
        if isinstance(link, dict):
            if link.get("small"):
                photo["preview_url"] = link["small"]
            if link.get("large"):
                photo["preview_url_large"] = link["large"]
    cta = meta.get(origin.CTA_META_KEY)
    if isinstance(cta, dict) and isinstance(cta.get("url"), str):
        structured["cta"] = {"label": str(cta.get("label") or ""), "url": cta["url"]}
    return True


class KeepLegacyGrid(Middleware):
    """While the window is open, an OpenAI host's answer keeps, in `structuredContent`,
    what the 0.4.12 grid reads. Every other call passes through untouched.

    Outside SplitPreviews, which takes the links out of the model's half and writes the
    text the model reads (grid_summary), and AttachOrigin, which moves the link under the
    grid to `_meta`: it sees the answer once both are done, and puts back only what the
    old grid reads. The text is left as SplitPreviews wrote it.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        if not keeps_legacy_grid(context):
            return await call_next(context)
        held: dict = {}
        token = _legacy_held.set(held)
        try:
            result = await call_next(context)
        finally:
            _legacy_held.reset(token)
        if _restore_legacy_grid(result, held):
            # Logged: how many answers the window served, and until when.
            logger.info("Kept the 0.4.12 grid's links and fields for an OpenAI host "
                        "(window until %s)", _utc(LEGACY_GRID_UNTIL))
        return result
