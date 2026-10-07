"""customize_spec tunes the kept tools and scrubs environment-specific bits."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pexafy_mcp import tooling


def _spec():
    """A stand-in spec: both kept operations, the similar operation (not kept, so left
    alone) and a servers block.

    Both kept operations, not one: customize_spec refuses a spec where a kept operation
    is missing, so a partial stand-in would be indistinguishable from real spec drift.
    """
    def _filters(extra=()):
        names = ["q", "orientation", "cursor", "color_name", "source", "license_type",
                 "after_date", "photographer", "score_threshold", "sort_by", *extra]
        return [{"name": n, "in": "query", "schema": {}} for n in names]

    spec = {
        "servers": [{"url": "https://internal.example.com"}],
        "paths": {
            "/api/v1/search/photos": {
                "get": {
                    "operationId": "search_photos_api_v1_search_photos_get",
                    "summary": "raw summary",
                    "parameters": _filters(),
                },
                "post": {
                    "operationId": "search_photos_by_image_api_v1_search_photos_post",
                    "summary": "raw summary",
                    "parameters": _filters(["text_alpha"]),
                },
            },
            "/api/v1/photos/{photo_id}/similar": {
                "get": {
                    "operationId": "photo_similar_api_v1_photos__photo_id__similar_get",
                    "summary": "raw summary",
                    "parameters": [
                        {"name": "photo_id", "in": "path", "schema": {}},
                        {"name": "per_page", "in": "query", "schema": {}},
                        {"name": "orientation", "in": "query", "schema": {}},
                        {"name": "cursor", "in": "query", "schema": {}},
                    ],
                },
            },
        },
    }
    spec["paths"]["/api/v1/search/photos"]["get"]["parameters"][0]["description"] = "old"
    return spec


SNAPSHOT = Path(tooling.__file__).resolve().parent / "assets" / "openapi.json"


def _shipped_spec():
    """The snapshot the server actually loads, not a hand-written stand-in."""
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _surface(spec):
    return {
        (path, method): {p["name"] for p in operation.get("parameters", [])}
        for path, methods in spec.get("paths", {}).items()
        for method, operation in methods.items()
        if (path, method) in tooling.KEEP_OPS
    }


def test_drops_servers_block():
    spec = _spec()
    tooling.customize_spec(spec)
    assert "servers" not in spec


def test_keeps_only_query_and_orientation():
    """The search surface is `q` + `orientation`. Every other filter is dropped:
    an assistant fills in whatever a tool offers, and each volunteered filter
    narrowed the catalogue for a constraint the user never gave (colour read as a
    brand palette down to 0.13% of the catalogue, one provider down to 14%,
    license_type for no effect at all). `cursor` went too: paging is a second concept
    to hold for a grid that is already the top 16 of nine million photos."""
    spec = _spec()
    tooling.customize_spec(spec)
    params = {p["name"] for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    assert params == {"q", "orientation"}, params


def test_the_shape_filter_is_one_value_everywhere_and_one_sentence_per_tool():
    """The schema is shared, the wording is not — and the split is the point.

    Shared: the type, the enum, the fact that it is a list. An enum that differed by
    tool would be a different value wearing one name, and a caller that has learnt
    `portrait` on one tool would have to learn it again on the next.

    Not shared: what the mistake looks like HERE. One description served every tool and
    it had been written for the text search, so it told the by-image tool to put the
    shape "here and not again inside `q`" — naming a parameter it did not have. The real
    trap is different on each: a word inside the sentence on the search; the REFERENCE
    on the by-image tool — the image they gave, or the photo they pointed at by
    `photo_id`: a portrait reference is not a request for portraits.
    """
    spec = _spec()
    tooling.customize_spec(spec)

    def _orientation(path, method):
        for p in spec["paths"][path][method]["parameters"]:
            if p["name"] == "orientation":
                return p
        raise AssertionError(f"no orientation on {method} {path}")

    search = _orientation("/api/v1/search/photos", "get")
    by_image = _orientation("/api/v1/search/photos", "post")

    # One value: same schema, same closed set, wherever it appears.
    assert search["schema"] == by_image["schema"]
    for shape in tooling.ORIENTATIONS:
        assert shape in json.dumps(by_image["schema"])

    # Two sentences, and not the same one.
    assert search["description"] != by_image["description"]

    # A tool never points at a parameter it does not have: `q` on the by-image tool is
    # the words of a `photo_id` reference, not a place to put the shape.
    assert f"`{tooling.PUBLIC_QUERY_PARAM}`" in search["description"]
    assert "`q`" not in by_image["description"]

    # Each one names its own trap, in its own terms.
    assert "a natural landscape" in search["description"]
    assert "reference image" in by_image["description"]
    assert "reference image the user gave" in by_image["description"]

    # And both still say the thing that is true of the filter everywhere: it is a hard
    # filter, and several shapes widen rather than narrow.
    for text in (search["description"], by_image["description"]):
        assert "hard filter" in text
        assert "Multiple values are allowed" in text
        assert text.lstrip().startswith("Omit")


def test_a_per_operation_override_replaces_only_what_it_names():
    """Merged over the shared entry, never substituted for it.

    The shared entry is where the binding half lives — `schema_replace` for the shape
    filter, `required` for `q`. An override that replaced the whole entry to change one
    sentence would silently drop the enum with it, and the API ignores an unknown shape
    in silence: "vertical" would come back as an unfiltered grid with nothing to see.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    by_image = next(p for p in spec["paths"]["/api/v1/search/photos"]["post"]["parameters"]
                    if p["name"] == "orientation")
    # The description came from the per-operation entry…
    assert "reference image" in by_image["description"]
    # …and everything else still came from the shared one.
    assert by_image["schema"] == tooling.ORIENTATION_SCHEMA


def test_every_per_operation_override_names_a_kept_parameter():
    """An override keyed on a parameter KEEP_PARAMS drops, or on an operation that has
    moved, is a sentence nobody will ever read — and it fails silently, which is how a
    correction gets written, committed, and never shipped."""
    for (path, method), params in tooling._param_overrides_by_operation().items():
        assert (path, method) in tooling.KEEP_OPS, (path, method)
        for name in params:
            assert name in tooling.KEEP_PARAMS[(path, method)], (path, method, name)


def test_rewrites_description_and_drops_summary():
    spec = _spec()
    tooling.customize_spec(spec)
    op = spec["paths"]["/api/v1/search/photos"]["get"]
    assert "summary" not in op
    assert "matched to the scene" in op["description"]
    # What a result carries is stated as facts, never as absolutes: a description that
    # orders the assistant about is grounds for a connector to be refused at review.
    assert "credit line to display" in op["description"]
    for imperative in ("ALWAYS", "Do NOT", "proactively", "you must show"):
        assert imperative not in op["description"], imperative
    # It speaks of photographs, and names the tool the file itself comes from under its
    # public name.
    assert "photograph" in op["description"]
    assert "photograph" in op["description"]
    assert tooling.PUBLIC_TOOL_NAMES["get_photo_file"] in op["description"]
    # Worded as an offer, not an order: no OpenAI rule bans guidance in a description,
    # but "close every reply" reads as an absolute, which review guidance treats as a
    # description ordering the assistant about.
    assert "every reply" not in op["description"]
    assert "photograph" in op["description"]


def test_hardcodes_value_sets_into_param_docs():
    spec = _spec()
    tooling.customize_spec(spec)
    by_name = {p["name"]: p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    # The one closed value set left is written into the parameter doc.
    for shape in tooling.ORIENTATIONS:
        assert shape in by_name["orientation"]["description"]
    # The sentence gets an example on its schema, under JSON Schema's own keyword.
    assert by_name["q"]["schema"]["examples"] == [tooling.SENTENCE_EXAMPLE]
    assert "example" not in by_name["q"]["schema"]


def test_orientation_ships_no_example_to_copy():
    """`orientation` must not advertise a value, at either level.

    An example is a suggestion to fill the field in, and this is the one parameter that
    should stay unset by default. It shipped `example: "landscape"` twice — on the
    parameter and on the string branch — and a caller then fills it in unasked, often
    with that exact literal, on queries whose own words ("a natural landscape") name a
    scene rather than a shape. `q` keeps its example; this one has none.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    by_name = {p["name"]: p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    orientation = by_name["orientation"]
    for keyword in ("example", "examples"):
        assert keyword not in orientation
        schema = orientation["schema"]
        assert keyword not in schema
        for branch in schema.get("anyOf", []):
            assert keyword not in branch, branch
        assert keyword not in tooling.ORIENTATION_SCHEMA


def test_orientation_doc_defaults_to_omitting_it():
    """The parameter doc has to open on the default, not on how to use the filter.

    The previous wording led with "this is the one filter the tool exposes, so it is
    where a format constraint belongs" and buried "leave it out" fourth, which is how a
    shape nobody asked for ends up on most calls. The doc states the default first and
    names the failure it has to prevent: a word inside the sentence read as a format.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    by_name = {p["name"]: p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    doc = by_name["orientation"]["description"]
    assert doc.startswith("Omit this parameter")
    # The four sources a shape must never be inferred from, and the observed one first.
    assert "a natural landscape" in doc
    assert "from the subject" in doc
    assert "hard filter" in doc


def test_orientation_takes_a_list_of_shapes():
    """The API declares an array and ORs the values: landscape+square returns both,
    either alone returns one. It shipped as a single
    string, which made this filter narrower than the API's — "anything but portrait"
    could not be said at all — and left the parameter doc telling a model to "repeat
    the parameter", which over MCP means an array.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    by_name = {p["name"]: p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    schema = by_name["orientation"]["schema"]
    branch = next(b for b in schema["anyOf"] if b.get("type") == "array")
    assert branch["items"]["enum"] == tooling.ORIENTATIONS


def test_the_public_name_is_not_the_wire_name():
    """The rename lives on the tool surface only. If it ever reached the spec, FastMCP
    would send the new name as the query parameter and the API — which drops what it
    does not know, in silence — would stop filtering with nothing to see."""
    spec = _spec()
    tooling.customize_spec(spec)
    names = {p["name"] for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    assert tooling.API_ORIENTATION_PARAM in names
    assert tooling.PUBLIC_ORIENTATION_PARAM not in names


def test_nullable_schema_keywords_land_on_the_string_branch():
    """`anyOf: [{string}, {null}]` — a maxLength left on the union constrains
    nothing that reads the branches."""
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    tooling.apply_schema_keywords(schema, {"maxLength": 150})
    assert schema["anyOf"][0]["maxLength"] == 150
    assert "maxLength" not in schema
    plain = {"type": "string"}
    tooling.apply_schema_keywords(plain, {"maxLength": 150})
    assert plain["maxLength"] == 150


def test_query_length_is_bound_in_the_schema_not_only_in_prose():
    """A limit stated in the description is advice; the same limit in the schema is
    what a client enforces. 250 characters is one precise sentence — and every extra
    clause pulls the meaning of the whole sentence towards the average of its parts."""
    spec = _spec()
    tooling.customize_spec(spec)
    by_name = {p["name"]: p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"]}
    q_schema = by_name["q"]["schema"]
    bound = q_schema.get("maxLength") or next(
        b["maxLength"] for b in q_schema.get("anyOf", []) if "maxLength" in b
    )
    assert bound == 250
    assert "250 characters" in by_name["q"]["description"]
    # It sends the shape to the filter under the name the TOOL exposes: the API's own
    # `orientation` exists on no tool surface, so pointing at it sends the model to a
    # parameter it cannot find.
    assert tooling.PUBLIC_ORIENTATION_PARAM in by_name["q"]["description"]
    assert "in `orientation`" not in by_name["q"]["description"]


def test_is_idempotent():
    spec = _spec()
    tooling.customize_spec(spec)
    once = copy.deepcopy(spec)
    tooling.customize_spec(spec)
    assert spec == once


def test_the_query_is_required_and_no_longer_nullable():
    """With every filter but `orientation` gone, a search with no words has nothing
    left to search by — so `q` stops being optional, and stops accepting null."""
    spec = _spec()
    tooling.customize_spec(spec)
    q = next(p for p in spec["paths"]["/api/v1/search/photos"]["get"]["parameters"] if p["name"] == "q")
    assert q["required"] is True
    assert "anyOf" not in q["schema"] and "default" not in q["schema"]


def test_a_kept_parameter_carries_no_title_from_the_spec():
    """The spec titles a parameter with its API name ("Q", "Orientation"), and the tool
    surface renames it: `english_search_sentence` titled "Q" names a parameter the tool
    does not have."""
    spec = _shipped_spec()
    tooling.customize_spec(spec)
    for path, method in tooling.KEEP_OPS:
        for p in spec["paths"][path][method]["parameters"]:
            assert "title" not in p.get("schema", {}), (method, path, p["name"])
    assert "title" not in tooling.ORIENTATION_SCHEMA


async def test_every_input_schema_is_standard_json_schema(monkeypatch):
    """What a host validates a call against: JSON Schema 2020-12, with no OpenAPI
    leftovers — `example` is OpenAPI's keyword, JSON Schema's is `examples`, a list — and
    no title naming the API's parameter instead of the tool's. All five tools: the file
    tool registers only with previews on."""
    from jsonschema import Draft202012Validator

    from pexafy_mcp import previews, server

    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    tools = await server.build_server().list_tools()
    assert {t.name for t in tools} == set(tooling.PUBLIC_TOOL_NAMES.values())
    for tool in tools:
        schema = tool.to_mcp_tool().inputSchema
        Draft202012Validator.check_schema(schema)
        for name, prop in schema.get("properties", {}).items():
            assert "title" not in prop, (tool.name, name)
            for node in (prop, *prop.get("anyOf", [])):
                assert "example" not in node, (tool.name, name)


def test_the_paging_block_leaves_the_output_schema():
    """`cursor` left the tools, so a `next_cursor` in the declared answer would
    offer something the caller can no longer ask for."""
    schema = {
        "properties": {"success": {}, "data": {}, "pagination": {}},
        "required": ["success", "data", "pagination"],
    }
    tooling.prune_output_schema(schema)
    assert "pagination" not in schema["properties"]
    assert schema["required"] == ["success", "data"]
    assert tooling.prune_output_schema(None) is None


def _same_values(before: dict, after: dict, values) -> None:
    import jsonschema
    for value in values:
        assert (jsonschema.Draft202012Validator(before).is_valid(value)
                == jsonschema.Draft202012Validator(after).is_valid(value)), value


def test_a_type_list_becomes_branches_of_one_type_each():
    """MCP Inspector's portability check: `type: ["string", "null"]` is legal JSON
    Schema, but a client that maps tool schemas onto a single-`type` dialect (Gemini's
    function declarations) may refuse the tool or drop the constraint. Split, the field
    accepts exactly the values it accepted, and a keyword of one type goes with it."""
    page = {"type": ["string", "null"], "format": "uri", "description": "The page."}
    before = copy.deepcopy(page)
    tooling.split_type_lists(page)
    assert page == {"description": "The page.",
                    "anyOf": [{"type": "string", "format": "uri"}, {"type": "null"}]}
    _same_values(before, page, (None, "https://example.com/photo/1", 3, ["x"]))

    width = {"type": ["integer", "null"], "minimum": 1}
    before = copy.deepcopy(width)
    tooling.split_type_lists(width)
    assert width == {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
    _same_values(before, width, (None, 0, 1, 4000, 2.5, "4000"))


def test_the_split_reaches_nested_schemas_and_leaves_values_alone():
    """Down `properties`, `items` and `$defs`; never into `default`, `enum` or
    `examples`, which hold values, not schemas."""
    schema = {
        "type": "object",
        "properties": {"data": {"type": "array", "items": {"$ref": "#/$defs/Photo"}}},
        "$defs": {"Photo": {
            "type": "object",
            "properties": {"width": {"type": ["integer", "null"]}},
            "default": {"type": ["kept", "as", "is"]},
            "examples": [{"type": ["kept"]}],
        }},
    }
    tooling.split_type_lists(schema)
    photo = schema["$defs"]["Photo"]
    assert photo["properties"]["width"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert photo["default"] == {"type": ["kept", "as", "is"]}
    assert photo["examples"] == [{"type": ["kept"]}]
    assert tooling.split_type_lists(None) is None


def test_a_union_already_there_is_kept_under_all_of():
    """Both unions must hold: dropping either would accept values the field refused."""
    field = {"type": ["string", "null"], "anyOf": [{"maxLength": 3}, {"type": "null"}]}
    before = copy.deepcopy(field)
    tooling.split_type_lists(field)
    assert "type" not in field and set(field) == {"allOf"}
    _same_values(before, field, (None, "ab", "abcd", 1))


def test_a_type_list_of_one_is_a_plain_type():
    field = {"type": ["string"], "maxLength": 3}
    tooling.split_type_lists(field)
    assert field == {"type": "string", "maxLength": 3}


def test_the_shipped_snapshot_exposes_exactly_the_allowed_parameters():
    """Every test above runs on a hand-written spec, which proves the filter runs but
    never what it lets through: the tools are built from assets/openapi.json, and that
    file is regenerated from the live API. Pinned against the real snapshot, this goes
    red the day the API adds a parameter — which would otherwise reach the tool schema
    and be filled in by an assistant — and the day it drops or renames one the tools
    depend on, which would otherwise be a 422 in production."""
    spec = _shipped_spec()
    tooling.customize_spec(spec)
    assert _surface(spec) == {key: set(names) for key, names in tooling.KEEP_PARAMS.items()}


def test_a_filter_the_api_gains_tomorrow_never_reaches_a_tool():
    """The snapshot is a regenerated artefact, so the guard has to hold for parameters
    nobody has written down yet — which is the whole difference between naming what
    stays and naming what goes."""
    spec = _shipped_spec()
    tomorrow = {
        "name": "safe_search",
        "in": "query",
        "required": False,
        "description": "Filter out explicit content.",
        "schema": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "default": None},
    }
    for path, method in tooling.KEEP_OPS:
        spec["paths"][path][method].setdefault("parameters", []).append(copy.deepcopy(tomorrow))
    tooling.customize_spec(spec)
    for key, names in _surface(spec).items():
        assert "safe_search" not in names, key


def test_an_endpoint_that_moved_stops_the_server_instead_of_shipping_raw_rest():
    """KEEP_OPS matches on (path, method). An endpoint that moves matches nothing, the
    tuning is skipped in silence, and what ships is the raw REST surface — every filter
    back, `q` optional and capped at the API's 500, plus the collection endpoints as
    read-only-annotated tools. It has to fail where the message can still name it."""
    spec = _spec()
    spec["paths"]["/api/v2/search/photos"] = spec["paths"].pop("/api/v1/search/photos")
    with pytest.raises(RuntimeError, match="spec drift"):
        tooling.customize_spec(spec)


def test_make_required_refuses_a_union_it_cannot_collapse():
    """A union it cannot narrow would ship `required: true` on a schema that still
    validates null — the contradiction the function exists to prevent. It has to fail
    loudly: with every filter gone, a search whose `q` is null searches nothing."""
    for any_of in ([{"type": "string"}, {"type": "integer"}, {"type": "null"}], [{"type": "null"}]):
        with pytest.raises(ValueError, match="non-null branches"):
            tooling._make_required({"name": "q", "schema": {"anyOf": any_of}})


def test_the_libraries_are_named_as_provenance_not_as_a_routing_rule():
    """People ask for a photo by naming a library they know, and this catalogue really
    is an index of those nine — so the best known are named. What must never appear is
    a rule keyed on a brand ("use this when someone asks for an Unsplash photo"): that
    stops describing the tool and starts routing on someone else's name."""
    spec = _spec()
    tooling.customize_spec(spec)
    description = spec["paths"]["/api/v1/search/photos"]["get"]["description"]
    for library in ("Unsplash", "Pexels", "Pixabay"):
        assert library in description, library
    # `source` is not in KEEP_PARAMS, so the libraries are named as the provenance of
    # Pexafy's OWN index — never as something a search could be narrowed to, nor as
    # services a query is relayed to ("in one query" read as a pass-through connector,
    # which OpenAI does not approve).
    assert "Pexafy's own index" in description
    assert "in one query" not in description
    # The wording people actually use, quoted as examples of a request. Generic only:
    # naming a library HERE — "a photo from Unsplash" as a phrasing that fires the tool —
    # reads as routing on someone else's brand, which is the line this test holds. The
    # libraries stay named once, as provenance, in the sentence checked above.
    for phrasing in ("free, stock, royalty-free", "royalty-free", "commercially usable"):
        assert phrasing in description, phrasing
    for brand_rule in ("a photo from Unsplash", "from Pexels", "from Pixabay"):
        assert brand_rule not in description, brand_rule
    assert "stock libraries such as Unsplash, Pexels and Pixabay" in description
    assert "source" not in tooling.KEEP_PARAMS[("/api/v1/search/photos", "get")]
    for banned in ("use this when someone asks for an Unsplash",
                   "for Pixabay images", "official Unsplash"):
        assert banned.lower() not in description.lower(), banned


def test_the_description_opens_on_what_the_tool_is_for():
    """The first sentences are the ones that win or lose the routing.

    They used to be: one line on the catalogue, then 306 characters of honesty clause
    ("not those sites themselves…") before any statement of use — the "when to use
    this" only started 700 characters in. A model choosing between this and a native
    image search read a paragraph of caveats first.

    So the order is the test: say it is image search, name the plain requests, and let
    the clauses follow. They are all still there, further down.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    description = spec["paths"]["/api/v1/search/photos"]["get"]["description"]
    opening = description[:340]
    # What it is, and the one thing that separates it from a generic image search: the
    # photographs themselves, as direct URLs. Both inside the first breath.
    assert "photograph" in opening
    assert "real photograph" in opening
    assert "photograph" in opening
    # The commonest requests of all, which the description never named before. They come
    # right after the opening, well ahead of any caveat.
    for plain in ("find me photos of", "show me images of", "I need a picture of"):
        assert plain in description, plain
    # The deliverable named without a search being asked for — "for a product page" was
    # the wording of a real prompt the tool answered only 6 times in 10.
    for deliverable in ("landing pages", "blog post", "slide", "newsletter"):
        assert deliverable in description, deliverable
    assert description.index("find me photos of") < description.index("does not generate")
    # And the caveats are NOT in the opening.
    assert "not those sites themselves" not in opening
    assert "does not generate" not in opening


def test_keywords_are_not_talked_down():
    """A short query has to feel welcome here.

    The text said "a described scene retrieves better than keywords", which reads as
    "keywords work poorly on this tool" — for the very requests worth catching ("photos
    of water bottles"). The semantic match and the full sentence are stated first, as
    what the engine is; keywords are then said to work too, never pitted against it.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    description = spec["paths"]["/api/v1/search/photos"]["get"]["description"]
    assert "Keywords work too" in tooling._param_overrides()["q"]["description"]
    assert "matched to the scene" in description
    # The old wording pitted one against the other on the tool description.
    assert "retrieves better than keywords" not in description


def test_the_query_goes_to_the_api_in_english():
    """Any language in, English out.

    The text used to claim a French or Spanish sentence "retrieves as well as an
    English one"; measured, English retrieves best. So the request is welcomed in any
    language — the multilingual triggers stay — and the model is told to translate the
    words it sends, on the tool and on the parameter.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    op = spec["paths"]["/api/v1/search/photos"]["get"]
    assert "English" in tooling._param_overrides()["q"]["description"]
    assert "retrieves as well as an English one" not in op["description"]
    q = next(p for p in op["parameters"] if p["name"] == "q")
    assert "English" in q["description"]



def test_the_real_photograph_trigger_is_positive_and_upfront():
    """"A real photo" is a trigger, not a footnote.

    Production logs show people typing it themselves — "an actual real travel photograph
    of…", "a real documentary photograph of…" — and the tool answered those requests while
    the description only mentioned the subject in the negative ("does not generate"),
    buried past the middle. The positive form now sits with the other triggers; the
    negative limit stays, further down, because OpenAI's criteria ask for stated limits.
    Neither is set against image generation: that is another tool, and a description
    that steers the model away from it is what OpenAI's fair-play rule forbids.
    """
    spec = _spec()
    tooling.customize_spec(spec)
    description = spec["paths"]["/api/v1/search/photos"]["get"]["description"]
    for trigger in ("a real photograph", "documentary photographs"):
        assert trigger in description, trigger
    assert "not illustrations" in description
    # Positive trigger first, negative limit after: the description opens on "Use this
    # when…", and what the tool does not do comes after the cases that call it.
    assert description.index("Use this when") < description.index("does not generate")
    assert "generated" not in description


# ── Fields this client adds to a response ────────────────────────────────────
# An undeclared top-level key is one any host is free to drop when it validates
# `structuredContent` against the declared schema — and a notice that is silently
# pruned looks exactly like a notice that was never built. `cta` had ridden along
# undeclared for a while; `budget` joined it and was nowhere to be seen. `cta` has
# since left the model's half of the answer for `_meta` (origin.CTA_META_KEY).

def _pruned_schema():
    from pexafy_mcp import tooling

    return tooling.prune_output_schema({
        "type": "object",
        "properties": {"success": {}, "data": {}, "meta": {}, "error": {}, "pagination": {}},
        "required": ["success", "data", "pagination"],
    })


def test_the_grid_footer_link_is_not_the_model_s():
    """It rides on `_meta` now, which no output schema describes."""
    assert "cta" not in _pruned_schema()["properties"]


def test_the_api_telemetry_is_not_declared():
    """`meta` is `request_id` and `took_ms`: the API's logs, nobody's answer."""
    schema = _pruned_schema()
    assert "meta" not in schema["properties"]
    assert "pagination" not in schema["required"]


def test_the_error_object_declares_no_request_id():
    from pexafy_mcp import tooling

    schema = tooling.prune_output_schema({
        "type": "object",
        "properties": {"error": {"anyOf": [{"$ref": "#/$defs/ApiError"}, {"type": "null"}]}},
        "$defs": {"ApiError": {"properties": {"code": {}, "message": {}, "request_id": {}},
                               "required": ["code", "message", "request_id"]}},
    })
    error = schema["$defs"]["ApiError"]
    assert list(error["properties"]) == ["code", "message"]
    assert error["required"] == ["code", "message"]


def test_no_block_declares_the_plan_name():
    props = _pruned_schema()["properties"]
    assert "plan" not in props["budget"]["properties"]


def test_the_account_corner_is_not_the_model_s():
    """Who is asking and the way to sign in are the grid's: they ride on `_meta`
    (budget.ACCOUNT_META_KEY), which no output schema describes."""
    assert "account" not in _pruned_schema()["properties"]


def test_the_budget_block_is_declared():
    assert "budget" in _pruned_schema()["properties"]


def test_the_budget_declares_exactly_what_the_model_is_sent():
    """Every field sent is declared — an undeclared one is a field a validating host may
    drop, and the notice went missing that way once — and nothing else is: the panel's
    wording and its button (`short`, `title`, `detail`, `cta_label`, `account_url`)
    reach the grid on `_meta`, where no schema prunes them."""
    from pexafy_mcp import budget

    props = _pruned_schema()["properties"]["budget"]["properties"]
    assert set(props) == set(budget.MODEL_FIELDS)
    for field in ("short", "title", "detail", "cta_label", "account_url"):
        assert field not in props, field


def test_pagination_is_still_removed():
    schema = _pruned_schema()
    assert "pagination" not in schema["properties"]
    assert "pagination" not in schema["required"]


def test_the_photo_sub_objects_declare_only_what_is_sent():
    """`prune_photo` keeps one size and one credit line. A schema that still lists
    five URLs and an HTML credit promises a model what no answer carries."""
    from pexafy_mcp import tooling

    schema = tooling.prune_output_schema({
        "type": "object",
        "properties": {"success": {}, "data": {"items": {"$ref": "#/$defs/Photo"}}},
        "$defs": {
            "Photo": {"properties": {"photo_id": {},
                                     "urls": {"$ref": "#/$defs/PhotoUrls"},
                                     "attribution": {"$ref": "#/$defs/Attribution"}}},
            "PhotoUrls": {"properties": {k: {} for k in ("thumb", "small", "regular",
                                                         "large", "full")},
                          "description": "Ready-to-use image links in five sizes."},
            "Attribution": {"properties": {"html": {}, "plain": {}}},
        },
    })
    assert list(schema["$defs"]["PhotoUrls"]["properties"]) == list(tooling.KEEP_PHOTO_URL_SIZES)
    assert list(schema["$defs"]["Attribution"]["properties"]) == list(tooling.KEEP_ATTRIBUTION_KEYS)
    assert "five sizes" not in schema["$defs"]["PhotoUrls"]["description"]
