"""Turn a caller's mistake into something it can act on, before the request leaves.

Four mistakes are caught, each because what happens without the guard is worse than an
error: the API's answer describes something else, or the call silently does less than
the caller believes.

`photo_id` that is not a UUID (by-image search, file tool) — the API answers a
malformed id with `500 SEARCH_ERROR — Search service temporarily unavailable`: the
service is fine, the id is not, and an assistant reads it as an outage and gives up.
The usual culprit is a rank. Results carry one, and a person names a photo by position
("more like the sixth"), so the number has to be traded back for that result's
`photo_id`.

A missing or blank sentence on the text search, the only thing it searches by — a
caller on the 0.4.12 snapshot, where `q` was optional and a filter alone was a valid
search, gets `TypeError: Missing required argument(s)`. And `required` constrains
presence, not value: an empty string reaches the API as a bare 400, a blank one
searches for whitespace.

A sentence over the cap, on either search — `maxLength` binds only a client that
validates before sending, and the API's own limit is larger.

A shape outside the enum — the API drops a value it does not know without an error, so
the caller gets an unfiltered grid it believes is filtered.

Each answer names the confusion and says what to send instead, which is what the next
turn is written from.
"""
from __future__ import annotations

import logging
import re

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import tooling

logger = logging.getLogger("pexafy.mcp")

# Pexafy ids are UUID7, but any UUID shape is accepted here: this guard exists to
# separate "a UUID" from "a rank, a title, a URL", not to police a version field the
# API is free to change.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# What a rank looks like once written down: "6", 6 or "#6".
_RANK = re.compile(r"^#?\d{1,3}$")

# One bare word. Still somebody's word — a first name sent as a `photo_id`, a query of
# one word — so it is logged by its length, like a sentence.
_WORD = re.compile(r"^[A-Za-z_-]{1,20}$")


def _described(value) -> str:
    """What a refused argument looked like, for the log, without what it said.

    The refusals are logged so their rate shows without a chat screenshot. The value
    itself stays out: a sentence is what somebody searched for, a single word can be a
    name, and a link or an id can point at their own picture. What the log keeps is its
    kind — a rank, a link, a word or a length. The one value shown is a shape this
    server names itself (tooling.ORIENTATIONS): nobody's words.
    """
    if value is None:
        return "nothing"
    if not isinstance(value, str):
        return type(value).__name__
    text = value.strip()
    if not text:
        return "blank"
    if text in tooling.ORIENTATIONS:
        return repr(text)
    if _RANK.match(text):
        return "a rank"
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        return "a URL"
    if _WORD.match(text):
        return f"a word ({len(text)} letters)"
    return f"{len(text)} characters"


# Every table below is keyed on the names the tools wear now. compat.py translates an old
# name before this middleware reads the call, so a caller on a published snapshot is
# guarded too — and a rename that forgot this file would leave a guard silently never
# firing.

# The tools that take a `photo_id`, and the argument that carries it. On the by-image
# search it is one of four ways to give the reference, checked only when sent. The file
# tool takes the same id, and unguarded, a rank sent there ends in an HTTP error nobody
# can act on.
GUARDED_ID_ARGS = {
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: "photo_id",
    tooling.PUBLIC_TOOL_NAMES["get_photo_file"]: "photo_id",
}

# The two tools that take the sentence, both capped at tooling.SENTENCE_MAX_LENGTH.
SENTENCE_ARGS = {
    tooling.PUBLIC_TOOL_NAMES["search_photos"]: tooling.PUBLIC_QUERY_PARAM,
    tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]: tooling.PUBLIC_QUERY_PARAM,
}

# The tool whose sentence is its entire query, so a missing or blank one is refused. On
# the by-image search the words are optional.
GUARDED_QUERY_ARGS = {
    tooling.PUBLIC_TOOL_NAMES["search_photos"]: tooling.PUBLIC_QUERY_PARAM,
}


class GuardPhotoId(Middleware):
    """Refuse a malformed argument before the call runs, with a message that says what
    to send: a missing or blank sentence, a sentence over the cap, an unknown shape, a
    `photo_id` that is not a UUID. Despite the name, it runs all four checks."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = getattr(context.message, "name", "")
        argument = GUARDED_ID_ARGS.get(tool)
        arguments = getattr(context.message, "arguments", None)
        query_arg = GUARDED_QUERY_ARGS.get(tool)
        if query_arg and isinstance(arguments, dict):
            value = arguments.get(query_arg)
            if not isinstance(value, str) or not value.strip():
                logger.info("Rejected a blank %s on %s (%s)", query_arg, tool, _described(value))
                raise ToolError(
                    f"`{query_arg}` is required: it is the only thing this tool searches "
                    "by. Send the scene in words, as one English sentence ('two people "
                    "sharing a bench in comfortable silence'); keywords work too."
                )
        # The schema's cap, enforced here: `maxLength` binds only a client that validates
        # before sending. A longer sentence would otherwise reach the API whole, or, past
        # the API's own limit, come back as an HTTP 422 nobody can act on. A limit that is
        # announced and not held is worse than none.
        sentence_arg = SENTENCE_ARGS.get(tool)
        if sentence_arg and isinstance(arguments, dict):
            sentence = arguments.get(sentence_arg)
            if isinstance(sentence, str) and len(sentence) > tooling.SENTENCE_MAX_LENGTH:
                logger.info("Rejected a %d-character %s on %s",
                            len(sentence), sentence_arg, tool)
                raise ToolError(
                    f"`{sentence_arg}` is capped at {tooling.SENTENCE_MAX_LENGTH} "
                    f"characters and this one is {len(sentence)}. One precise sentence "
                    "about the subject, the setting and the light finds more than a "
                    "piled-up brief: every extra clause pulls the meaning of the whole "
                    "sentence towards the average of its parts. Send the scene, not the "
                    "brief."
                )
        # A shape outside the enum is refused rather than swallowed. The schema declares
        # the enum, but the API drops a value it does not know without an error: `["wide"]`
        # would return photos of every shape, with a success, to a caller who believes it
        # filtered.
        shapes =arguments.get(tooling.PUBLIC_ORIENTATION_PARAM) if isinstance(arguments, dict) else None
        if isinstance(shapes, str):
            # One shape sent bare: made a list here, in the arguments themselves, so the
            # check below sees it and the tool's own validation receives a list.
            shapes = arguments[tooling.PUBLIC_ORIENTATION_PARAM] = [shapes]
        if isinstance(shapes, (list, tuple)):
            unknown = [s for s in shapes if s not in tooling.ORIENTATIONS]
            if unknown:
                logger.info("Rejected unknown shape(s) on %s: %s", tool,
                            ", ".join(_described(u) for u in unknown))
                raise ToolError(
                    f"{', '.join(repr(u) for u in unknown)} is not a photo shape. "
                    f"`{tooling.PUBLIC_ORIENTATION_PARAM}` takes "
                    f"{', '.join(tooling.ORIENTATIONS)} — nothing else. Send the shape "
                    "the person asked for, or omit the parameter."
                )
        if argument and isinstance(arguments, dict):
            value = arguments.get(argument)
            # Any type: a rank sent as a JSON number would otherwise meet the tool's own
            # validation, whose error names nothing the caller can act on.
            text = "" if value is None else str(value).strip()
            if text and not _UUID.match(text):
                # Logged so the rate of this mistake is visible without a chat screenshot.
                logger.info("Rejected a non-UUID %s on %s (%s)", argument, tool, _described(text))
                raise ToolError(_not_a_photo_id(text, tool))
        return await call_next(context)


def _not_a_photo_id(text: str, tool: str) -> str:
    shown = text if len(text) <= 60 else text[:57] + "..."
    if _RANK.match(text):
        return (
            f"{shown!r} is not a photo_id. A short number like this is a position "
            "— a `rank` names a photograph, it is not what this parameter takes. "
            "Every result carries its own `photo_id` beside its rank, a UUID such "
            f"as {tooling.PHOTO_ID_EXAMPLE}. Take the `photo_id` of the "
            "photograph the person meant and call again with that."
        )
    message = (
        f"{shown!r} is not a photo_id. `photo_id` takes the UUID a Pexafy result "
        f"carries, such as {tooling.PHOTO_ID_EXAMPLE}. Take the `photo_id` of "
        "the photograph the person meant and call again with that."
    )
    if tool == tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]:
        message += " A picture from anywhere else goes in `image_url` instead."
    return message
