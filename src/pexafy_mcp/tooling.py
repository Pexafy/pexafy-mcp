"""What the model sees of the tools, and the names the other modules key on.

The text search is generated from the Pexafy OpenAPI spec, which is written for REST
clients. `customize_spec` reshapes the spec at load time, before server.py builds the
tools from it:

  - keeps only the parameters named in KEEP_PARAMS;
  - rewrites the text search's description in the imperative, trigger first: what the
    tool is, then the requests that should fire it in the words people type, then the
    caveats. A description is read as routing evidence, and its opening sentences are
    the ones weighed against the prompt;
  - writes the parameter docs and schemas: the sentence is required and capped, and the
    one closed value set (ORIENTATIONS) goes into the shape filter's doc and enum.

Which endpoints become tools is decided by the route_maps in server.py. The by-image
tool is written by hand there, and borrows the POST operation's tuned parameter docs.

This module also holds the registry of public tool names and the older names compat.py
still answers to, the public and API names of the two renamed parameters, and what
comes off each result: `prune_photo` strips the fields nobody reads from every
photograph, and `prune_output_schema` keeps the declared output schema in step.

Every text below is read by a model, so it is held to OpenAI's app-submission rules and
to Anthropic's directory criteria: it may say what THIS server does and when the USER's
request calls for it; it may not trigger on a need the model infers on its own, favour
or disparage another service, steer the model away from another tool, or tell the model
how to behave beyond the use of the tool itself. tests/test_served_texts_compliance.py
holds the formulas that crossed those lines once.
"""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy

# The API's parameter names, which the spec and the query string keep, and the names the
# tools expose. The rename happens on the tool surface only (server.py: an ArgTransform
# on the generated tool, the signature of the hand-written one), so what leaves for the
# API is unchanged; compat.py translates a call that still uses the API's names. The
# public names carry the condition of use, which a caller reads every time it fills the
# argument in.
API_QUERY_PARAM = "q"
PUBLIC_QUERY_PARAM = "english_search_sentence"
API_ORIENTATION_PARAM = "orientation"
PUBLIC_ORIENTATION_PARAM = "explicit_orientation_filter"

# The whole parameter surface of each operation this module tunes, named positively.
#
# An allow-list rather than a deny-list, because the spec is a snapshot regenerated from
# the live API by prepare.sh. A list of parameters to drop covers only those that
# existed when it was written, so a parameter the API gains later would reach the tool
# schema unnoticed. Naming what stays makes a new API parameter a no-op and a renamed
# one a failing test — see the drift check at the end of customize_spec.
#
# What is left out, and why the tools do not expose it:
#   - the filters (color_name, color_hex, color_tolerance, source, license_type,
#     after_date, photographer): a caller fills in whatever a tool offers, and a filter
#     that was not asked for narrows the catalogue. `orientation` stays because a format
#     is a constraint a person states — a vertical Short, a banner — that no wording of
#     the sentence expresses.
#   - score_threshold and sort_by: relevance scores sit in a narrow band, so a guessed
#     threshold passes everything or nothing, and sort_by=newest overrides relevance.
#   - per_page, limit, fields: the page size is set server-side
#     (server._grid_page_size), and `fields` would starve the detail panel.
#   - cursor: the grid is a single page of results.
#   - text_alpha, the weight of words against the image in a combined search: unsent,
#     the API applies its own default, and follows it if that default ever moves.
KEEP_PARAMS = {
    ("/api/v1/search/photos", "get"): frozenset({API_QUERY_PARAM, API_ORIENTATION_PARAM}),
    # No tool is generated from the POST (route_maps excludes it): the by-image tool is
    # written by hand in server.py and borrows the doc of the parameters kept here. Its
    # sentence is not among them — server.py describes it, and caps it with the text
    # search's limit.
    ("/api/v1/search/photos", "post"): frozenset({API_ORIENTATION_PARAM}),
}

# The operations customize_spec tunes, and refuses to run without. Which operations
# become tools is decided by the route_maps in server.py.
KEEP_OPS = frozenset(KEEP_PARAMS)

# The one closed value set on the tools, hardcoded rather than fetched.
ORIENTATIONS = ["landscape", "portrait", "square"]

# The cap on the search sentence, in characters: the `maxLength` of both searches, the
# figure the sentence's description states, and the limit guards.py enforces on a
# client that does not validate before sending.
SENTENCE_MAX_LENGTH = 250

# The names the five tools wear on the MCP surface. One registry, because a tool name is
# a key in several places — guards, origin, compat, selection, the widget's tool proxy,
# and the titles, status texts and grid meta in server.py — and a rename that misses one
# is a tool that silently stops being guarded, or loses its grid. Prose that names a
# tool interpolates it from here.
#
# The two searches keep the names 0.4.x published. OpenAI re-scans a published app's
# tools: a tool that disappears from `tools/list` is removed at once, and a new name is
# offered only after its automated checks pass (developers.openai.com/plugins/deploy/
# app-review), so a rename leaves every installed user without the tool in between. A
# kept name is an update instead, and the previous definition stays live during review.
# Longer, descriptive names were measured and did no better (0/5 either way). The three
# tools 1.0.0 adds are new under any name, so theirs say what they do.
PUBLIC_TOOL_NAMES = {
    "search_photos": "search_photos",
    "search_photos_by_image": "search_photos_by_image",
    "get_photo_file": "get_photo_file_by_photo_id",
    "get_selected_photos": "get_grid_selected_photos",
    "connect_account": "connect_account",
}

# Names an older snapshot may still send, which compat.py translates to the current
# ones: the long names the two searches carried on preprod, and the short names briefly
# tried for the file and selection tools. The 0.4.x search names need no entry (they
# are the current ones); `get_similar_photos` is mapped in compat.RENAMED_TOOLS.
LEGACY_TOOL_NAMES = {
    "search_photos_from_unsplash_pexels_pixabay_by_text": PUBLIC_TOOL_NAMES["search_photos"],
    "search_photos_from_unsplash_pexels_pixabay_by_image": PUBLIC_TOOL_NAMES["search_photos_by_image"],
    "get_photo_file": PUBLIC_TOOL_NAMES["get_photo_file"],
    "get_selected_photos": PUBLIC_TOOL_NAMES["get_selected_photos"],
}

# A list, as the API declares it: the REST parameter is repeatable and the shapes OR
# together, so "anything but portrait" is expressible. The `enum` on the items matters —
# the API ignores an unknown value silently, so without it "vertical" would come back as
# an unfiltered grid with no error.
#
# No `examples`, unlike the sentence. An example is a suggestion to fill the field in,
# and this parameter should stay unset unless a shape was asked for: it is a hard filter
# that drops every photo of the other shapes before ranking. No `title` either (see
# customize_spec).
ORIENTATION_SCHEMA = {
    "anyOf": [
        {"type": "array", "items": {"type": "string", "enum": list(ORIENTATIONS)}},
        {"type": "null"},
    ],
}

# The `examples` value of the text search's sentence, which some clients prefill
# verbatim: a short, plain scene rather than a long one.
SENTENCE_EXAMPLE = "two people sharing a bench in comfortable silence"

# The id the texts show as an example of a `photo_id`, and the example value on the
# by-image tool's `photo_id` schema: an assistant that has a real id to copy makes fewer
# guesses.
PHOTO_ID_EXAMPLE = "019e1ecb-0039-7da6-b1ca-987ee4d337c0"


def _tool_descriptions(file_tool: bool = True, grid: bool = True) -> dict[tuple[str, str], str]:
    """The description of each generated tool, by operation: the text search only.

    The POST operation has none: no tool is generated from it, and the by-image tool
    that replaces it carries its own description, in server.py. Only the POST's
    parameters are tuned here, because that tool borrows their wording. The file tool is
    named only when it is registered, and the grid only where there is one (both need
    previews).
    """
    file_clause = (f"; the file itself comes from {PUBLIC_TOOL_NAMES['get_photo_file']}"
                   if file_tool else "")
    grid_clause = (" In a host that renders it, the grid shows them; the answer omits image links."
                   if grid else "")
    # Every trigger is something the user says. The one case the model sees coming
    # before the user asks — a piece of writing that usually carries photographs — is
    # an OFFER, and the call waits for the user's yes: a search the user did not ask for
    # is the "overly broad triggering" OpenAI refuses and the unrequested call Anthropic
    # refuses. "Where can I find free photos of…" is a request for free photographs in
    # so many words, and it names no other service; it is the phrasing an assistant was
    # measured answering with a list of websites.
    return {
        ("/api/v1/search/photos", "get"): (
            "Use this when the user asks for a real photograph, or states a need that a photograph fills, and the picture is not already in the conversation.\n"
            "\n"
            "Call it when they ask outright: “find me photos of…”, “show me images of…”, “I need a picture of…”, “do you have a photo of…”, “where can I find free photos of…”, in any language.\n"
            "Call it when they describe the need without asking to search: “I'm writing an article and need a header image”, “my landing page needs a hero image”, “I need something to illustrate this”.\n"
            "Call it when they ask for something meant to carry photographs: “write an article with pictures”, “a deck with images”.\n"
            "Call it when they name only the picture, with no reason given: “a photo of a white mug”, “a train in Japan”.\n"
            "If the user is working on something that usually carries photographs — an article, a page, a deck, a post — without asking for any, you may offer to look for some; call it only once they accept.\n"
            "\n"
            "This covers requests for photographs for articles, blog posts, websites, landing pages, products, slides, documents, reports, social posts, ads, newsletters, banners, thumbnails, covers, posters, backgrounds, wallpapers and mood boards; photographs that illustrate a concept, explain a subject or accompany a piece of writing; and any request for free, stock, royalty-free, commercially usable, real or documentary photographs.\n"
            "\n"
            "A request for a visual is not always a request for a photograph: this tool finds photographs that already exist — not illustrations, drawings, logos, icons, diagrams or screenshots — and it does not generate, edit or upscale an image, nor look for a named person.\n"
            "\n"
            f"It searches Pexafy's own index of millions of free-to-use photographs from stock libraries such as Unsplash, Pexels and Pixabay, matched to the scene described. Each result carries a `rank`, a `photo_id`, its licence, the credit line to display and an image link{file_clause}.{grid_clause}"
        ),
    }


def _param_overrides() -> dict[str, dict]:
    """Per-parameter overrides, applied on every kept operation that has the parameter.

    Keyed by the API's parameter names (`q`, `orientation`), not the public ones: they
    are applied to the OpenAPI spec, and server.py renames the parameters on the tool
    surface only afterwards (`english_search_sentence`, `explicit_orientation_filter`).
    Keyed by name rather than by operation, so the schema of a value is written once and
    cannot drift between the tools that take it; what a parameter says on ONE tool is
    `_param_overrides_by_operation`.

    `schema` merges keywords into the parameter's JSON Schema; `schema_replace` swaps it
    outright where the spec's own type does not suit MCP (see ORIENTATION_SCHEMA). A
    description binds nothing, a schema does. `required` on `q` makes the sentence
    mandatory: with the filters gone, the words are all there is to search by.
    """
    return {
        API_QUERY_PARAM: {
            "description": (
                "Required. Describe the desired photograph in one concise English sentence, focusing on its visible subject and scene.\n"
                "\n"
                "Be specific: “Two colleagues laughing in a bright open-plan office” is more effective than “People office”. Keywords work too — send the user's own words, in English, rather than inventing detail they did not ask for.\n"
                "\n"
                f"Maximum {SENTENCE_MAX_LENGTH} characters. Describe one coherent scene, focusing on what should appear in the photograph rather than its intended use.\n"
                "\n"
                f"Specify the requested photo shape or orientation in {PUBLIC_ORIENTATION_PARAM}, not here.\n"
                "\n"
                "Example: “An old man sitting at a café table he has visited every morning for thirty years”."
            ),
            "examples": [SENTENCE_EXAMPLE],
            # `minLength` beside the cap: `required` alone lets an empty string through,
            # and an empty `q` would reach the API as a 400 the caller can do nothing with.
            "schema": {"maxLength": SENTENCE_MAX_LENGTH, "minLength": 1},
            "required": True,
        },
        API_ORIENTATION_PARAM: {
            # The trap on the text search: the one failure seen on the wire.
            "description": _orientation_description(
                f"a word inside `{PUBLIC_QUERY_PARAM}` — “landscape” in “a natural "
                "landscape” names the scene, not the format"
            ),
            "schema_replace": ORIENTATION_SCHEMA,
        },
    }


def _param_overrides_by_operation() -> dict[tuple[str, str], dict[str, dict]]:
    """What a parameter says on ONE operation, merged over `_param_overrides()`.

    Only the keys named here replace the shared entry's; the schema, the enum and
    `required` still come from the shared entry, so one value cannot become two.

    The one entry is the shape filter's description on the by-image tool. What a shape
    must not be inferred from differs per tool: a word inside the sentence on the text
    search, the reference on the by-image tool — the image they gave, or the photograph
    they pointed at — whose shape is not a request for that shape.
    """
    return {
        ("/api/v1/search/photos", "post"): {
            API_ORIENTATION_PARAM: {
                "description": _orientation_description(
                    "the reference image the user gave — its shape is a property of the "
                    "picture they had, not a constraint on the ones they want"
                ),
            },
        },
    }


def _code_list(values, last: str = ", ") -> str:
    """Values in backticks, comma-separated; `last` joins the final two (", " or " or ")."""
    quoted = [f"`{value}`" for value in values]
    return ", ".join(quoted[:-1]) + last + quoted[-1]


def _orientation_description(trap: str) -> str:
    """The shape filter's description, around what one tool must not infer a shape from.

    It opens on the default (omit it), says why (a hard filter), names what a shape must
    never be read off — `trap`, the one clause that differs per tool — and ends on the
    values.
    """
    all_but_portrait = ", ".join(f"'{shape}'" for shape in ORIENTATIONS if shape != "portrait")
    return (
        "Omit this parameter unless the user explicitly asks for a photo shape or orientation in their own words, or names a format whose shape is part of its definition — a vertical Short, a banner, a square post, a phone wallpaper.\n"
        "\n"
        "This is a hard filter, not a preference: it removes photos with other shapes before ranking, so using a shape the user did not request can exclude the best matches.\n"
        "\n"
        f"Never infer the shape from {trap} — nor from the subject, the composition, or a purpose that fixes no shape (“a photo for my blog post”, “something for my landing page”). If you are choosing a value rather than repeating one explicitly requested by the user, omit it.\n"
        "\n"
        f"Allowed values: {_code_list(ORIENTATIONS)}. Multiple values are allowed: two shapes return more than one alone, though any filter still returns less than none. For example, “anything but portrait” means `[{all_but_portrait}]`."
    )


def _make_required(parameter: dict) -> None:
    """Turn an optional query parameter into a required one, schema included.

    An optional string is rendered `anyOf: [{type: string}, {type: null}]` with a
    null default. Flipping `required` alone would leave a parameter that a client
    must send and may still send as null; the nullable branch and the default go
    with it.
    """
    parameter["required"] = True
    schema = parameter.setdefault("schema", {})
    if "anyOf" in schema:
        branches = [b for b in schema["anyOf"] if isinstance(b, dict) and b.get("type") != "null"]
        if len(branches) != 1:
            # Leaving the union in place ships the very contradiction this function
            # exists to prevent: `required: true` on a schema that still validates
            # null. With every filter gone, a search with a null `q` searches nothing.
            raise ValueError(
                f"_make_required({parameter.get('name')!r}): cannot drop the nullable "
                f"branch of an anyOf with {len(branches)} non-null branches "
                f"({schema['anyOf']!r}) — the parameter would be required and still "
                "accept null. Narrow the union in the OpenAPI snapshot first."
            )
        schema.pop("anyOf")
        schema.update(branches[0])
    schema.pop("default", None)
    parameter.pop("default", None)


# ── What comes off every photograph before it leaves ─────────────────────────
# Every field of every result goes into the model's context, on every search and every
# refinement: sixteen photos of twenty-five fields measured 36,784 characters, most of
# it read by nobody.
#
# Three kinds go:
#
#   Nobody's, at all — `blur_hash`, `relevance_score` and `color_hex` have zero
#   readers in this repo, widget included. `source_photo_id` repeats `photo_id` (16 of
#   16 results when measured). `image_url` says what `urls` already says.
#
#   A second and third description — the grid's caption reads `alt_description` first
#   (see fields() in widget.py), so the long AI `description` and the provider's
#   `source_description` are two more paragraphs per photo for a line nobody shows.
#
#   The photographer, three times — `attribution.plain` already reads "Photo by X on
#   Y", which is the credit the card draws. `photographer_full_name` stays because the
#   viewer names the author on its own line.
#
# `uploaded_on` and `color_name` go with them: nothing reads them, and a field costs
# its weight on every single search.
REMOVE_PHOTO_FIELDS = frozenset({
    "blur_hash",
    "relevance_score",
    "color_hex",
    "source_photo_id",
    "image_url",
    "description",
    "source_description",
    "photographer_url",
    "photographer_username",
    "uploaded_on",
    "color_name",
})

# The sizes a caller is actually given. The API sends five variants of one photograph
# (18.7% of the payload when measured); the widget uses none of them — it draws the
# signed preview — and a caller needs one link. `regular` is 1280 pixels for every
# source; anything bigger is get_photo_file or the photo's own page.
KEEP_PHOTO_URL_SIZES = ("regular",)

# `attribution` is an object of {html, plain}. The card reads `plain`; `html` has no
# reader anywhere and is the larger half.
KEEP_ATTRIBUTION_KEYS = ("plain",)

# ── Text a third party wrote ─────────────────────────────────────────────────
# A photograph's description and its photographer's name come from the library it was
# found in, or from whoever uploaded it there, and they reach the model inside the
# answer it reads — the credit line too, which is built from the name. None of it is an
# instruction, but it can be written to look like one: a "name" of three hundred words,
# a description that breaks the line and starts again as somebody else, text the person
# never sees in invisible characters (zero-width, bidirectional overrides, the Unicode
# tag block, variation selectors spelling a message one byte each, fillers that draw a
# blank). So each such field is cut to FREE_TEXT_MAX_LENGTH characters and loses its
# control and invisible characters; a line break or a tab becomes a space, the grid
# shows these fields on one line anyway.
FREE_TEXT_MAX_LENGTH = 300
FREE_TEXT_PHOTO_FIELDS = ("alt_description", "photographer_full_name")
# Control, format and lone surrogate code points…
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
# …and every other code point Unicode lists as drawing nothing (Default_Ignorable_Code_
# Point, DerivedCoreProperties.txt; `unicodedata` does not expose it): the 256 variation
# selectors, the Hangul fillers, the combining grapheme joiner, the unassigned rest of
# the tag plane. Plus U+2800, the blank Braille pattern: not on that list, as blank.
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
# Two kinds are kept, one at a time, where writing needs them. The joiners, which
# Persian, the Indic scripts and emoji sequences are written with: one, between two
# characters — a run of them is a message in binary, and one beside a space or at an
# end joins nothing.
_JOINERS = frozenset({"\u200c", "\u200d"})
# And VS15 and VS16, the text and emoji presentation selectors: one, right after a
# character drawn both ways — a heart, a sun, a keycap digit. Anywhere else a
# variation selector is a hidden byte.
_PRESENTATION_SELECTORS = frozenset({"\ufe0e", "\ufe0f"})
# Such a character is a symbol (category So), save the keycap bases and a few marks,
# letters and arrows (Unicode's emoji-variation-sequences.txt).
_EMOJI_BASES_BEYOND_SO = frozenset(
    "#*0123456789\u203c\u2049\u2139\u2194\u25fb\u25fc\u25fd\u25fe\u2934\u2935\u3030\u303d")


def _invisible(ch: str) -> bool:
    if unicodedata.category(ch) in _DROPPED_CATEGORIES:
        return True
    code = ord(ch)
    return code >= 0xAD and any(low <= code <= high for low, high in _INVISIBLE_RANGES)


def _drawn_both_ways(ch: str) -> bool:
    return unicodedata.category(ch) == "So" or ch in _EMOJI_BASES_BEYOND_SO


def clean_free_text(value, limit: int = FREE_TEXT_MAX_LENGTH):
    """Third-party text as the model may read it: one line, no control or invisible
    character — a joiner or a presentation selector only where a script or an emoji
    needs one — and `limit` characters at most (an ellipsis marks the cut). Anything
    that is not a string is returned as it is."""
    if not isinstance(value, str):
        return value
    out: list[str] = []
    for ch in value:
        before = out[-1] if out else " "
        if ch.isspace():
            if before != " ":
                out.append(" ")
        elif ch in _JOINERS:
            if before != " " and before not in _JOINERS:
                out.append(ch)
        elif ch in _PRESENTATION_SELECTORS:
            if _drawn_both_ways(before):
                out.append(ch)
        elif not _invisible(ch):
            out.append(ch)
    # A joiner with nothing after it to join — a space, the end — goes as well.
    text = "".join(
        ch for i, ch in enumerate(out)
        if ch not in _JOINERS or (i + 1 < len(out) and out[i + 1] != " ")
    ).strip(" ")
    if len(text) > limit:
        text = text[: limit - 1].rstrip(" \u200c\u200d") + "…"
    return text


# Words a source writes where it has no name: "Photo by Unknown on Pixabay", and a
# missing surname stringified, "nympha57 None" (seen in the VS Code grid, 2026-10-01).
_NO_NAME = {"unknown", "none", "null", "undefined", "n/a", "nan"}


def clean_photographer(name):
    """The photographer's name without placeholder words; "" when nothing is left —
    the API's own "Unknown photographer" included — or when what is left is a bare
    number, a source's user id ("Photo by 3345557 on Pixabay"), which names nobody."""
    if not isinstance(name, str):
        return name
    words = name.split()
    kept = [w for w in words if w.lower() not in _NO_NAME]
    if len(kept) < len(words) and [w.lower() for w in kept] == ["photographer"]:
        kept = []
    cleaned = " ".join(kept)
    return "" if cleaned.isdigit() else cleaned


def _clean_credit(photo: dict) -> None:
    """Name the photographer the API credits — the full name, else the username
    (build_attribution in the API), which pruning then drops — without placeholder
    words, and take those out of every credit line too, which may carry one even where
    the name field is empty: "Photo by Unknown on Pixabay" becomes "Photo on Pixabay",
    "Photo by nympha57 None on Pexels" "Photo by nympha57 on Pexels"."""
    name = photo.get("photographer_full_name")
    username = photo.get("photographer_username")
    if isinstance(name, str) or isinstance(username, str):
        photo["photographer_full_name"] = (clean_photographer(name or "")
                                           or clean_photographer(username or ""))
    attribution = photo.get("attribution")
    if isinstance(attribution, dict):
        for k, v in list(attribution.items()):
            if isinstance(v, str):
                attribution[k] = _clean_credit_line(v)


def _clean_credit_line(line: str) -> str:
    match = _CREDIT_LINE.match(line)
    if not match:
        return line
    lead, who, rest = match.groups()
    who = clean_photographer(who)
    return f"{lead} by {who}{rest}" if who else f"{lead}{rest}"


# "Photo by <name> on <source>…", the shape every credit line the API writes has.
_CREDIT_LINE = re.compile(r"^(Photo) by (.*?)( on .*)$", re.S)


def prune_photo(photo: dict) -> None:
    """Take the dead weight off one photograph, in place, and clean the text a third
    party wrote in it (clean_free_text)."""
    if not isinstance(photo, dict):
        return
    _clean_credit(photo)
    for name in REMOVE_PHOTO_FIELDS:
        photo.pop(name, None)
    urls = photo.get("urls")
    if isinstance(urls, dict):
        photo["urls"] = {k: v for k, v in urls.items() if k in KEEP_PHOTO_URL_SIZES}
    attribution = photo.get("attribution")
    if isinstance(attribution, dict):
        photo["attribution"] = {
            k: clean_free_text(v) for k, v in attribution.items() if k in KEEP_ATTRIBUTION_KEYS
        }
    for name in FREE_TEXT_PHOTO_FIELDS:
        if isinstance(photo.get(name), str):
            photo[name] = clean_free_text(photo[name])


# Response fields dropped from the answer AND from the declared output schema — one
# list for both, so what is declared and what is sent cannot drift apart
# (prune_result applies it to the body, prune_output_schema to the schema).
#
#   pagination  the tools take no `cursor`, so a schema that announced
#               `pagination.next_cursor` would tell an assistant it can ask for page two
#               when it cannot.
#   meta        the API's own telemetry: `request_id` and `took_ms`. OpenAI's app
#               guidelines ask that a result carry no "diagnostic, telemetry, or internal
#               identifiers — such as session IDs, trace IDs, request IDs, timestamps, or
#               logging metadata — unless they are strictly required to fulfill the
#               user's query", and neither is: no model, and no person, acts on either.
REMOVE_RESULT_FIELDS = {"pagination", "meta"}

# …and, for the same reason, the request id the API writes into its error object.
REMOVE_ERROR_FIELDS = frozenset({"request_id"})

# Fields this client adds to the RESPONSE after the API has answered (see
# server._enrich_response); the per-photo equivalent is ADD_PHOTO_FIELDS below.
# Declared, because an undeclared top-level key is one any host is free to drop when it
# validates structuredContent against the published schema — and a notice that is
# silently pruned looks exactly like one that was never sent.
#
# What only the grid draws is not among them: it rides on the result's `_meta`, the half
# of an answer a host keeps from the model — the link to this answer on the website
# (origin.CTA_META_KEY), and the account corner: who is asking, the way to sign in, the
# panel's wording and its button (budget.ACCOUNT_META_KEY). `budget` keeps what the
# model reads, its numbers and one sentence (budget.MODEL_FIELDS).
ADD_RESULT_FIELDS = {
    "budget": {
        "type": "object",
        "description": (
            "Present only when the caller is near the end of an allowance — the daily "
            "one without an account, the monthly one with. What is left of it, and, "
            "for a caller with no account, the one sentence that says what lifts the "
            "limit. Also carried by a refusal, where it describes the wall itself."
        ),
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["day", "month"],
                "description": "Which allowance this counts: the daily one or the monthly one. Both exist with or without an account, so `signed_in` does not tell them apart.",
            },
            "limit": {"type": "integer", "description": "Searches allowed in that period."},
            "remaining": {"type": "integer", "description": "Searches left in that period."},
            "used": {"type": "integer", "description": "Searches spent in that period."},
            "state": {
                "type": "string",
                "enum": ["warning", "exhausted"],
                "description": "`warning` while there is room left, `exhausted` once there is none.",
            },
            "signed_in": {
                "type": "boolean",
                "description": "Whether the caller has an account. False is the anonymous allowance.",
            },
            "message": {"type": "string", "description": "The whole notice in words, for a host with no UI."},
        },
    },
    # The words that go with an empty grid. Present only on a refusal, and the reason
    # a refusal is a result at all rather than a tool error: an error renders no
    # widget, so the wall would have had nowhere to appear.
    "notice": {
        "type": "string",
        "description": (
            "Why this answer is empty, when it is. A plan limit was reached: the "
            "allowance and when it returns. There is nothing to retry until then."
        ),
    },
}

# Fields this client adds to every photo after the API has answered (see
# server._enrich_response). They are in the payload a model reads, so they belong in
# the declared schema — the spec cannot carry them, it does not know they exist, and
# the MCP spec asks a server to return results that conform to the schema it declares.
# `rank` is the photo's position in this answer. The grid draws no number on a result,
# but a person names a photo by position ("the second one"), and the rank is what turns
# that into a `photo_id`.
_RANK_DESCRIPTION = (
    "This photograph's place in this answer, from 1. A person may name a result "
    "by position (“the second one”); the tools take that result's `photo_id`, "
    "not its rank."
)
ADD_PHOTO_FIELDS = {
    "rank": {
        "type": "integer",
        "description": (
            _RANK_DESCRIPTION + " The grid draws no number on a result: the only numbers "
            "the person sees are on the photographs they liked."
        ),
    },
    "preview_url": {
        "type": "string",
        "description": "Signed 480-pixel thumbnail the inline grid renders. It expires.",
    },
    "preview_url_large": {
        "type": "string",
        "description": "Signed 1280-pixel preview the grid's photo viewer renders. Never expires.",
    },
}


def photo_fields(*, grid: bool = True) -> dict:
    """ADD_PHOTO_FIELDS as this server sends them, with the grid or without it. Without
    one no photo carries the grid's image links (previews.inject_preview_urls adds none),
    and the rank is all there is — a rank that says nothing of a grid."""
    if grid:
        return ADD_PHOTO_FIELDS
    return {"rank": {"type": "integer", "description": _RANK_DESCRIPTION}}

# Fields the API already declares, whose published description does not say what a
# caller should do with them. Their description is rewritten on the schema borrowed
# from the spec; they exist, so they are not added.
RETITLE_PHOTO_FIELDS = {
    "orientation": f"The shape of this photograph — {_code_list(ORIENTATIONS, ' or ')}, the values `{PUBLIC_ORIENTATION_PARAM}` takes.",
    "source_image_url": "The photograph's page on the library it came from — where to send someone who wants the source.",
}


def _photo_object(schema: dict) -> dict | None:
    """The per-photo object inside a search envelope, `$ref` followed.

    At the point this runs the schema is not inlined yet: `data.items` is a `$ref`
    into `$defs`, so updating it in place would silently change nothing.
    """
    node = ((schema.get("properties") or {}).get("data") or {}).get("items")
    if not isinstance(node, dict):
        return None
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        node = (schema.get("$defs") or {}).get(ref.split("/")[-1])
    if isinstance(node, dict) and isinstance(node.get("properties"), dict):
        return node
    return None


def _sub_object(schema: dict, node) -> dict | None:
    """The object one photo field points at, `$ref` followed.

    Same reason as `_photo_object`: at this point `urls` and `attribution` are still
    refs into `$defs`, so writing on the ref itself would change nothing.
    """
    if not isinstance(node, dict):
        return None
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        node = (schema.get("$defs") or {}).get(ref.split("/")[-1])
    if isinstance(node, dict) and isinstance(node.get("properties"), dict):
        return node
    return None


def _keep_only(node: dict, keep) -> None:
    """Leave one object with only the keys the answer actually carries."""
    node["properties"] = {k: v for k, v in node["properties"].items() if k in keep}
    if isinstance(node.get("required"), list):
        node["required"] = [r for r in node["required"] if r in keep]


def _error_objects(schema: dict) -> list[dict]:
    """The object(s) the `error` field can be, `$ref` followed.

    The spec declares it nullable — `anyOf: [{$ref: ApiError}, {type: null}]` — so the
    object sits one branch down, and, as for the photo, behind a ref.
    """
    node = (schema.get("properties") or {}).get("error")
    if not isinstance(node, dict):
        return []
    found = []
    for branch in [node, *node.get("anyOf", []), *node.get("oneOf", [])]:
        target = _sub_object(schema, branch)
        if target is not None and all(target is not seen for seen in found):
            found.append(target)
    return found


def prune_result(data) -> None:
    """Take out of a search answer, in place, what REMOVE_RESULT_FIELDS and
    REMOVE_ERROR_FIELDS name — the body half of what prune_output_schema does to the
    declared schema."""
    if not isinstance(data, dict):
        return
    for name in REMOVE_RESULT_FIELDS:
        data.pop(name, None)
    error = data.get("error")
    if isinstance(error, dict):
        for name in REMOVE_ERROR_FIELDS:
            error.pop(name, None)


def prune_output_schema(schema: dict | None, *, grid: bool = True) -> dict | None:
    """Align a tool's declared output with what the tool actually returns — with the
    grid or without it (photo_fields)."""
    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name in REMOVE_RESULT_FIELDS:
            properties.pop(name, None)
        properties.update(ADD_RESULT_FIELDS)
    for error in _error_objects(schema):
        for name in REMOVE_ERROR_FIELDS:
            error["properties"].pop(name, None)
        if isinstance(error.get("required"), list):
            error["required"] = [r for r in error["required"] if r not in REMOVE_ERROR_FIELDS]
    photo = _photo_object(schema)
    if photo is not None:
        # The spec's wording sends a reader to `fields`, which these tools do not take.
        photo["description"] = "One photograph of this answer."
        photo["properties"].update(photo_fields(grid=grid))
        # A model reads the schema to know what it receives: "the shape of this
        # photograph" tells it what to do with a field whose original label only gave
        # its type.
        for name, text in RETITLE_PHOTO_FIELDS.items():
            field = photo["properties"].get(name)
            if isinstance(field, dict):
                field["description"] = text
        # Declared and sent must be the same thing: a schema that still announces
        # `blur_hash` describes a payload this server no longer produces.
        for name in REMOVE_PHOTO_FIELDS:
            photo["properties"].pop(name, None)
        if isinstance(photo.get("required"), list):
            photo["required"] = [r for r in photo["required"]
                                 if r not in REMOVE_PHOTO_FIELDS]
        # `urls` and `attribution` are cut down on the way out too (prune_photo keeps
        # one size and one credit line). Left whole here, the declared schema would
        # promise `urls.full` and `attribution.html`, which no answer carries — and a
        # model reads the schema, not this file, to know what it is about to get.
        urls = _sub_object(schema, photo["properties"].get("urls"))
        if urls is not None:
            _keep_only(urls, KEEP_PHOTO_URL_SIZES)
            urls["description"] = "The photograph's image file, 1280 pixels wide, for a page, a document or a download. `urls` carries this one variant and no other."
        credit = _sub_object(schema, photo["properties"].get("attribution"))
        if credit is not None:
            _keep_only(credit, KEEP_ATTRIBUTION_KEYS)
            credit["description"] = "The credit line to display, already written."
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [r for r in required if r not in REMOVE_RESULT_FIELDS]
    return schema


def apply_schema_keywords(schema: dict, keywords: dict) -> None:
    """Merge validation keywords into a parameter schema, nullable or not.

    A nullable parameter is rendered `anyOf: [{type: string}, {type: null}]`, and a
    keyword left on the union constrains nothing that reads the branches — so it goes
    on the string branch when there is one, and on the schema itself otherwise.
    """
    branches = [b for b in schema.get("anyOf", []) if isinstance(b, dict) and b.get("type") == "string"]
    for target in branches or [schema]:
        target.update(keywords)


def customize_spec(spec: dict, *, file_tool: bool = True, grid: bool = True) -> None:
    """Mutate the OpenAPI spec in place to tune the kept tools for an LLM."""
    overrides = _param_overrides()
    per_operation = _param_overrides_by_operation()
    descriptions = _tool_descriptions(file_tool, grid)

    # Drop the `servers` block: tool calls go through the configured httpx client
    # (server.py), never the spec's servers — and the snapshot must not surface any
    # environment-specific host. Defence in depth; the public schema is already clean.
    spec.pop("servers", None)

    matched = set()
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if (path, method) not in KEEP_OPS:
                continue
            matched.add((path, method))
            if (path, method) in descriptions:
                op["description"] = descriptions[(path, method)]
                op.pop("summary", None)  # description carries the full WHEN guidance
            params = op.get("parameters")
            if not params:
                params = op["parameters"] = []
            allowed = KEEP_PARAMS[(path, method)]
            op["parameters"] = [p for p in params if p.get("name") in allowed]
            here = per_operation.get((path, method), {})
            for p in op["parameters"]:
                # The spec titles a parameter with its API name ("Q", "Orientation"),
                # and server.py renames it on the tool surface: the title would name a
                # parameter the tool does not have.
                (p.get("schema") or {}).pop("title", None)
                ov = overrides.get(p.get("name"))
                # What this parameter means ON THIS TOOL, over what it means everywhere.
                # Merged, not substituted: the entry above still carries the schema, the
                # enum and `required`, so one value cannot become two.
                local = here.get(p.get("name"))
                if local:
                    ov = {**(ov or {}), **local}
                if not ov:
                    continue
                if "description" in ov:
                    p["description"] = ov["description"]
                if "examples" in ov:
                    # JSON Schema's `examples`, a list, on the schema the tool is built
                    # from. OpenAPI's `example` is not JSON Schema, and a
                    # parameter-level one never reaches a tool.
                    p.setdefault("schema", {})["examples"] = list(ov["examples"])
                if ov.get("required"):
                    _make_required(p)
                if "schema_replace" in ov:
                    # Replaced, not merged: a keyword merged onto `anyOf: [array, null]`
                    # would sit beside the wrong branch instead of correcting it. Copied,
                    # because the GET and the POST share this override object.
                    p["schema"] = deepcopy(ov["schema_replace"])
                if "schema" in ov:
                    # The binding half: a maxLength in the description is advice, the
                    # same value in the schema is a constraint the client enforces.
                    apply_schema_keywords(p.setdefault("schema", {}), ov["schema"])

    # KEEP_OPS matches on (path, method), so an operation that moves matches nothing and
    # every tuning above is skipped silently. What would ship then is the raw REST
    # surface: the filters KEEP_PARAMS keeps off the tools, a `q` that is optional and
    # capped at the API's own limit, descriptions written for curl — and, since the
    # exclusion route_maps in server.py are anchored on the same paths, the collection
    # endpoints as tools. It fails here instead, at load time, naming the operation.
    if matched != KEEP_OPS:
        missing = ", ".join(f"{m.upper()} {p}" for p, m in sorted(KEEP_OPS - matched))
        declared = ", ".join(sorted(spec.get("paths", {}))) or "(none)"
        raise RuntimeError(
            f"OpenAPI spec drift: no operation matched {missing}. The spec declares: "
            f"{declared}. The LLM tuning was skipped for it and the raw REST surface "
            "would be exposed. Update tooling.KEEP_PARAMS and the route_maps in "
            "server.build_server() to the new path, then re-run ./prepare.sh."
        )
