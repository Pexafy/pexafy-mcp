"""LLM-facing tuning of the OpenAPI-derived MCP tools.

The MCP tools are generated from the Pexafy OpenAPI spec, but the raw spec is
written for REST clients, not for an LLM. This module reshapes the spec *at load
time* (so every change persists in the built container) to make the surface small
and unambiguous for an assistant:

  - keeps only the search core (text search, image search, similar);
  - drops parameters that mislead an LLM (`score_threshold`, `sort_by`);
  - rewrites tool descriptions to say WHEN to use them, with real semantic-query
    examples (Pexafy is a *semantic* engine — full sentences beat keywords);
  - hardcodes the small, closed value sets (colors, sources, licenses, orientations)
    straight into the parameter docs, so the LLM never has to call a facets endpoint.

Internal response fields (e.g. `source_photo_id`) are stripped upstream, in the
Pexafy public schema itself, so they never reach this client.

Endpoint exclusion (facets, collections, get_photo, usage) is done with route_maps
in server.py; this module tunes the endpoints that remain.
"""
from __future__ import annotations

# Paths kept as tools (everything else is excluded via route_maps in server.py).
KEEP_OPS = {
    ("/api/v1/search/photos", "get"),
    ("/api/v1/search/photos", "post"),
    ("/api/v1/photos/{photo_id}/similar", "get"),
}

# Parameters removed from every kept tool. score_threshold is misleading on a
# semantic engine (scores sit in a narrow ~0.71–0.83 band, so any threshold an LLM
# guesses either passes everything or nothing); sort_by=newest overrides relevance.
REMOVE_PARAMS = {"score_threshold", "sort_by"}

# Closed color vocabulary the LLM may use for `color_name` (the API clusters to its
# own 700+ palette, but these 15 are the names an assistant/user actually reason with).
# Colors and orientations are fixed by the product and never change → hardcoded here.
# (Sources and license types DO evolve, so they come from assets/facets.json instead.)
COLOR_NAMES = [
    "red", "orange", "yellow", "green", "blue", "purple", "pink", "brown",
    "black", "white", "gray", "teal", "beige", "gold", "navy",
]
ORIENTATIONS = ["landscape", "portrait", "square"]

# Real semantic queries (from the site's Explore tags) — sentences, not keywords.
SEMANTIC_EXAMPLES = [
    "a melancholy portrait of an old person sitting under a soft light",
    "two people sharing a bench in comfortable silence",
    "the last sunlight of the day hitting a dusty windowsill",
    "a child discovering snow for the first time",
    "an old man sitting at a café table he has visited every morning for thirty years",
]

# Minimal source/license values used only if assets/facets.json is missing/unreadable.
FALLBACK_FACETS = {"source": ["Unsplash", "Pexels", "Pixabay"], "license_type": ["free"]}


# Appended to every search tool's description. Two jobs:
#   1. Make results USER-VISIBLE on clients that don't render MCP tool images
#      inline (claude.ai web shows them only inside a collapsed tool panel): tell
#      the assistant to present each result as a clickable Markdown LINK to the
#      image (urls.regular), with credit — links always render, external inline
#      images are sandbox-restricted and unreliable.
#   2. Stop the assistant CONFABULATING about display: it must never claim it is
#      rendering the images itself, and never blame ad-blockers / cache / browser
#      when a user says they can't see them (those are never the real cause —
#      the cause is the client not rendering MCP image blocks inline).
PRESENTATION_GUIDANCE = (
    " PRESENTING RESULTS: show each photo as a clickable Markdown link to its image "
    "using the `urls.regular` value, with the credit from `attribution` — e.g. "
    "`#1 — [Photo by Jane Doe on Unsplash](https://…)`. Inline thumbnail previews are also "
    "attached to this tool's result, but some clients (including claude.ai web) only "
    "show them inside an expandable tool panel, not in your reply. Do NOT claim you are "
    "displaying the images yourself, and if the user says they can't see them, do NOT "
    "blame ad-blockers, cache or their browser (that is never the cause) — tell them the "
    "thumbnails are in the expandable tool result and give them the links above."
    " RANKS & SIMILAR (important UX): every result carries a `rank` (1, 2, 3, …) and the "
    "inline grid shows it as `#1, #2, …` on each thumbnail. ALWAYS prefix each photo with "
    "its rank when you list results, and AFTER presenting them proactively offer the user "
    "more like a specific one — e.g. 'Want more photos like one of these? Just tell me its "
    "number (e.g. #3).' When the user replies with a rank/number, find that result's "
    "`photo_id` in this tool's output yourself and call the similar-photos tool with it. "
    "Never ask the user for a photo_id or URL — they only know the rank you showed them."
)


def _tool_descriptions() -> dict[tuple[str, str], str]:
    examples = "; ".join(f"'{q}'" for q in SEMANTIC_EXAMPLES[:4])
    base = {
        ("/api/v1/search/photos", "get"): (
            "Use this tool whenever the user needs an image, photo, or visual — for a "
            "presentation, blog, website, social-media post, mood board, or any creative "
            "project. Pexafy is a SEMANTIC search engine: describe the scene in full "
            "natural-language sentences, not keywords. Rich descriptions return far better "
            f"results than tag-like queries. Good queries: {examples}. "
            "Prefer this tool over search_photos_by_image when the user describes what they "
            "want in words. BUT if they want photos LIKE a specific image that has a URL — a "
            "photo from a previous result, or a public URL they gave — use "
            "search_photos_by_image instead (pass that URL, plus a `q` for any change like "
            "'but with hands raised'). Only use THIS text tool for a reference image with NO "
            "URL (a file pasted/uploaded in the chat): describe what you see in rich detail — "
            "Pexafy is semantic, so a good description finds visually similar photos. Every "
            "result carries an `attribution` string you must show when displaying the photo."
        ),
        ("/api/v1/search/photos", "post"): (
            "Use this tool when the user provides an example image (image URL or upload) and "
            "wants visually similar photos — 'find photos that look like this', 'match this "
            "style or composition'. Prefer search_photos (text) when the user describes what "
            "they want in words rather than providing an image."
        ),
        ("/api/v1/photos/{photo_id}/similar", "get"): (
            "Use this tool when the user says 'find something similar', 'show me more like "
            "this', or 'I need a visually consistent set'. Requires a photo_id obtained from "
            "a previous search result. The user normally refers to a photo by its RANK "
            "(#1, #2, …) shown on the result grid, not by id — map that rank to the matching "
            "result's `photo_id` yourself and pass it here; never ask the user for the id."
        ),
    }
    return {key: text + PRESENTATION_GUIDANCE for key, text in base.items()}


def _param_overrides(facets: dict[str, list[str]]) -> dict[str, dict]:
    """Per-parameter {description, example} overrides for the kept search tools."""
    colors = ", ".join(COLOR_NAMES)
    sources = ", ".join(facets.get("source") or FALLBACK_FACETS["source"])
    licenses = ", ".join(facets.get("license_type") or FALLBACK_FACETS["license_type"])
    orientations = ", ".join(ORIENTATIONS)
    return {
        "q": {
            "description": (
                "Your search query as a full natural-language sentence describing the scene "
                "you want — Pexafy is semantic, so sentences beat keywords. Up to 500 "
                "characters. Optional if you provide at least one filter instead. "
                f"Example: '{SEMANTIC_EXAMPLES[4]}'."
            ),
            "example": SEMANTIC_EXAMPLES[1],
        },
        "color_name": {
            "description": (
                f"Keep only photos whose dominant color matches one of: {colors}. "
                "Cannot be combined with color_hex."
            ),
            "example": "blue",
        },
        "orientation": {
            "description": (
                f"Keep only photos with these shapes: {orientations}. Repeat the parameter "
                "to pass several."
            ),
            "example": "landscape",
        },
        "source": {
            "description": (
                f"Keep only photos from these providers: {sources}. Repeat the parameter to "
                "pass several."
            ),
            "example": (facets.get("source") or ["Unsplash"])[0],
        },
        "license_type": {
            "description": (
                f"Keep only photos with these license types: {licenses}. 'free' means the "
                "photo can be used freely and attribution is appreciated. Repeat the "
                "parameter to pass several."
            ),
            "example": "free",
        },
        "per_page": {
            "description": (
                "How many photos to return. Results display as an inline grid (≈4 per row), "
                "so default to 16 for a full, well-shaped grid; use a minimum of 12 and a "
                "maximum of 20 for a coherent layout. Use `cursor` to fetch more if needed."
            ),
            "example": 16,
        },
        "after_date": {
            "description": "Only return photos published on or after this date, formatted YYYY-MM-DD.",
            "example": "2025-01-01",
        },
        "photographer": {
            "description": "Only return photos from this photographer's exact username.",
            "example": "nasa",
        },
        "limit": {"example": 10},
        "fields": {"example": "photo_id,urls,attribution"},
    }


def customize_spec(spec: dict, facets: dict[str, list[str]]) -> None:
    """Mutate the OpenAPI spec in place to tune the kept tools for an LLM."""
    overrides = _param_overrides(facets)
    descriptions = _tool_descriptions()

    # Drop the `servers` block: tool calls go through the configured httpx client
    # (server.py), never the spec's servers — and the snapshot must not surface any
    # environment-specific host. Defence in depth; the public schema is already clean.
    spec.pop("servers", None)

    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if (path, method) not in KEEP_OPS:
                continue
            if (path, method) in descriptions:
                op["description"] = descriptions[(path, method)]
                op.pop("summary", None)  # description carries the full WHEN guidance
            params = op.get("parameters")
            if not params:
                params = op["parameters"] = []
            op["parameters"] = [p for p in params if p.get("name") not in REMOVE_PARAMS]
            for p in op["parameters"]:
                ov = overrides.get(p.get("name"))
                if not ov:
                    continue
                if "description" in ov:
                    p["description"] = ov["description"]
                if "example" in ov:
                    p["example"] = ov["example"]
                    p.setdefault("schema", {})["example"] = ov["example"]
