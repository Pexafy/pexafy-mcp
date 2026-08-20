"""The server builds offline and exposes exactly the search core."""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from pexafy_mcp import server

EXPECTED_TOOLS = {"search_photos", "search_photos_by_image", "get_similar_photos"}


async def test_build_server_registers_search_core():
    mcp = server.build_server()
    names = {t.name for t in await mcp.list_tools()}
    assert EXPECTED_TOOLS <= names, names


async def test_excluded_routes_are_not_tools():
    # usage / facets / collections / popular-searches / get_photo are excluded
    # (route_maps in build_server). None of them should surface as a tool.
    mcp = server.build_server()
    names = {t.name for t in await mcp.list_tools()}
    leaked = {n for n in names if any(x in n for x in ("usage", "facet", "collection", "popular"))}
    assert not leaked, leaked


async def test_tool_titles_cover_every_search_tool():
    # A title must exist for each tool we ship (drives the client display name).
    assert set(server.TOOL_TITLES) == EXPECTED_TOOLS


def test_decode_base64_image_sniffs_png():
    # 1x1 transparent PNG.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    )
    import base64

    data, ctype, name = server._decode_base64_image(base64.b64encode(png).decode())
    assert data == png
    assert ctype == "image/png"
    assert name == "upload"


def test_decode_base64_image_tolerates_data_uri_prefix():
    import base64

    raw = base64.b64encode(b"\xff\xd8\xff hello").decode()
    data, ctype, _ = server._decode_base64_image(f"data:image/jpeg;base64,{raw}")
    assert ctype == "image/jpeg"
    assert data.startswith(b"\xff\xd8\xff")


def test_decode_base64_image_rejects_garbage_and_empty():
    with pytest.raises(ToolError):
        server._decode_base64_image("!!!not base64!!!")
    with pytest.raises(ToolError):
        server._decode_base64_image("")


def test_decode_base64_image_rejects_oversize(monkeypatch):
    import base64

    monkeypatch.setattr(server, "_MAX_IMG_BYTES", 4)
    big = base64.b64encode(b"123456789").decode()
    with pytest.raises(ToolError):
        server._decode_base64_image(big)


async def test_fetch_image_rejects_non_http_url():
    # URL validation happens before any network call.
    with pytest.raises(ToolError):
        await server._fetch_image("ftp://example.com/x.jpg")
    with pytest.raises(ToolError):
        await server._fetch_image("not-a-url")


async def test_search_by_image_requires_a_source():
    # No image_url / image_file / image_base64 → a clear ToolError, no network.
    with pytest.raises(ToolError):
        await server.search_photos_by_image()


def _cta(method, path, q=None):
    import httpx
    url = "http://x" + path + (f"?q={q}" if q else "")
    return server._pexafy_cta(httpx.Request(method, url))


def test_cta_text_search_links_to_query():
    cta = _cta("GET", "/api/v1/search/photos", q="two+cats")
    assert cta["label"] == "More at Pexafy"
    assert cta["url"].endswith("/?q=two%2Bcats") or "?q=" in cta["url"]


def test_cta_similar_links_to_photo_page():
    cta = _cta("GET", "/api/v1/photos/abc-123/similar")
    assert cta["label"] == "More at Pexafy"
    assert cta["url"].endswith("/photos/abc-123/")


def test_cta_image_search_is_try_it():
    cta = _cta("POST", "/api/v1/search/photos")
    assert cta["label"] == "Try it at Pexafy"


def test_cta_none_for_other_paths():
    assert _cta("GET", "/api/v1/something/else") is None


async def test_every_tool_declares_itself_read_only():
    """A host uses `readOnlyHint` to decide whether a call needs confirming, and
    Anthropic's connector directory rejects a submission whose tools carry no
    read-only/destructive hint. All three tools search and return; none writes."""
    from pexafy_mcp.server import build_server

    for tool in await build_server().list_tools():
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert annotations.readOnlyHint is True, tool.name
        assert annotations.destructiveHint is False, tool.name
        assert annotations.openWorldHint is True, tool.name
        assert annotations.title, tool.name


async def _image_tool_schema():
    for tool in await server.build_server().list_tools():
        if tool.name == "search_photos_by_image":
            return tool.to_mcp_tool().inputSchema
    raise AssertionError("search_photos_by_image is not registered")


async def test_the_uploaded_file_parameter_matches_the_apps_sdk_schema():
    """OpenAI's app-directory scan refuses the server outright if it does not.

    Their contract, from the Apps SDK reference: every file object declares all
    four properties, `download_url` and `file_id` are required, `mime_type` and
    `file_name` are not, and nothing else may appear. The schema Pydantic infers
    from `image_file: dict | None` satisfies none of that — hence the literal in
    FILE_PARAM_SCHEMA, and hence this test, which is the only thing standing
    between an inferred schema and a rejected submission.
    """
    assert (await _image_tool_schema())["properties"]["image_file"] == {
        "type": "object",
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


async def test_the_uploaded_file_parameter_stays_optional():
    """Optional by absence from `required`, never by a nullable union: an
    `anyOf` wrapper hides the properties the scan reads."""
    schema = await _image_tool_schema()
    assert "image_file" not in schema.get("required", [])
    assert "anyOf" not in schema["properties"]["image_file"]


async def test_the_file_parameter_is_declared_to_the_host():
    """The schema is only reached because `openai/fileParams` names the field."""
    for tool in await server.build_server().list_tools():
        if tool.name == "search_photos_by_image":
            assert (tool.to_mcp_tool().meta or {}).get("openai/fileParams") == ["image_file"]


async def test_every_tool_describes_its_results():
    """A tool with no output schema is one a model has to guess at, and ChatGPT
    shows the gap to the user as a badge on the tool. The two generated tools get
    theirs from the spec; the hand-written one had none until it borrowed it."""
    for tool in await server.build_server().list_tools():
        assert tool.to_mcp_tool().outputSchema, tool.name


async def test_the_by_image_tool_borrows_the_search_result_schema():
    """Borrowed, not copied: both tools post to the same endpoint and return the
    same envelope, so a second copy here would be free to drift from the spec."""
    schemas = {t.name: t.to_mcp_tool().outputSchema for t in await server.build_server().list_tools()}
    assert schemas["search_photos_by_image"] == schemas["search_photos"]


async def test_every_parameter_is_described():
    """An undescribed parameter is one a model has to guess at from its name alone.

    The generated tools inherit descriptions from the spec; the hand-written
    by-image tool inherited nothing from a Python signature, and shipped twelve
    bare parameters — which is what cost the listing its "Parameter descriptions"
    point on Smithery.
    """
    for tool in await server.build_server().list_tools():
        for name, schema in tool.to_mcp_tool().inputSchema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{name}"


async def test_the_by_image_filters_repeat_the_spec_word_for_word():
    """Borrowed from the operation this tool posts to, not paraphrased: the two
    describe the same query parameters of the same endpoint, and a paraphrase here
    would drift the day someone edits the spec."""
    from pexafy_mcp.server import _load_facets, _load_openapi_spec, _spec_param_descriptions
    from pexafy_mcp import tooling

    spec = _load_openapi_spec()
    tooling.customize_spec(spec, _load_facets())
    from_spec = _spec_param_descriptions(spec, "/api/v1/search/photos", "post")
    assert from_spec, "the operation whose wording is borrowed disappeared from the spec"

    tools = {t.name: t for t in await server.build_server().list_tools()}
    properties = tools["search_photos_by_image"].to_mcp_tool().inputSchema["properties"]
    for name, text in from_spec.items():
        if name in properties:
            assert properties[name]["description"] == text, name
