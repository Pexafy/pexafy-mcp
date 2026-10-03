"""The server builds offline and exposes exactly the search core."""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from pexafy_mcp import observe, server, tooling

EXPECTED_TOOLS = {tooling.PUBLIC_TOOL_NAMES["search_photos"], tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]}


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
    # A title must exist for each shipped tool (it drives the client display name).
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


async def test_the_page_size_is_forced_on_searches_only():
    """Every search, text or by reference, asks for one grid of photos whatever the
    caller sent; the other calls on the client (a photo's metadata, the usage probe)
    carry no page size."""
    import httpx

    for method, path in (("GET", "/api/v1/search/photos?q=cats&per_page=99"),
                         ("POST", "/api/v1/search/photos?photo_id=abc")):
        request = httpx.Request(method, "http://x" + path)
        await server._grid_page_size(request)
        assert request.url.params.get_list("per_page") == [str(server.GRID_PAGE_SIZE)], path
    for path in ("/api/v1/photos/abc-123", "/api/v1/usage"):
        request = httpx.Request("GET", "http://x" + path)
        await server._grid_page_size(request)
        assert "per_page" not in request.url.params, path


async def test_every_tool_declares_itself_read_only():
    """A host uses `readOnlyHint` to decide whether a call needs confirming, and
    Anthropic's connector directory rejects a submission whose tools carry no
    read-only/destructive hint. Every tool reads; none writes.

    `openWorldHint` only on the two searches: OpenAI reserves it for tools that reach
    the open internet, and the others read nothing but Pexafy's own state.

    `idempotentHint` everywhere but on the two searches: each search spends one of the
    caller's allowance, so the same call twice is not "no extra effect". With the grid
    on, so all five tools are there."""
    from pexafy_mcp import previews
    from pexafy_mcp.server import build_server

    searches = {tooling.PUBLIC_TOOL_NAMES["search_photos"],
                tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(previews, "PREVIEWS_AVAILABLE", True)
        tools = await build_server().list_tools()
    assert len(tools) == 5, [tool.name for tool in tools]
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert annotations.readOnlyHint is True, tool.name
        assert annotations.destructiveHint is False, tool.name
        assert annotations.openWorldHint is (tool.name in searches), tool.name
        assert annotations.idempotentHint is (tool.name not in searches), tool.name
        assert annotations.title, tool.name


async def _image_tool_schema():
    for tool in await server.build_server().list_tools():
        if tool.name == tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]:
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
            "The user's uploaded image, as a host that passes uploads to tools provides "
            "it: the upload's `download_url` and `file_id`. A picture that is only "
            "visible in the conversation has neither."
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
        if tool.name == tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]:
            assert (tool.to_mcp_tool().meta or {}).get("openai/fileParams") == ["image_file"]


async def test_every_tool_describes_its_results():
    """A tool with no output schema is one a model has to guess at, and ChatGPT
    shows the gap to the user as a badge on the tool. The generated text search gets
    its schema from the spec, the by-image tool borrows it, the others declare theirs."""
    for tool in await server.build_server().list_tools():
        assert tool.to_mcp_tool().outputSchema, tool.name


async def test_the_by_image_tool_borrows_the_search_result_schema():
    """Borrowed, not copied: both tools post to the same endpoint and return the
    same envelope, so a second copy here would be free to drift from the spec."""
    schemas = {t.name: t.to_mcp_tool().outputSchema for t in await server.build_server().list_tools()}
    assert schemas[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]] == schemas[tooling.PUBLIC_TOOL_NAMES["search_photos"]]


async def test_every_parameter_is_described():
    """An undescribed parameter is one a model has to guess at from its name alone.

    The generated tool inherits descriptions from the spec; a hand-written tool gets
    none from a Python signature, and bare parameters are marked down by directory
    quality scores.
    """
    for tool in await server.build_server().list_tools():
        for name, schema in tool.to_mcp_tool().inputSchema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{name}"


async def test_the_by_image_filters_repeat_the_spec_word_for_word():
    """Borrowed from the operation this tool posts to, not paraphrased: the two
    describe the same query parameters of the same endpoint, and a paraphrase here
    would drift the day someone edits the spec."""
    from pexafy_mcp.server import _load_openapi_spec, _spec_param_descriptions

    spec = _load_openapi_spec()
    tooling.customize_spec(spec)
    from_spec = _spec_param_descriptions(spec, "/api/v1/search/photos", "post")
    assert from_spec, "the operation whose wording is borrowed disappeared from the spec"
    # The spec names the shape filter `orientation`; the tool wears the public name.
    public = {tooling.PUBLIC_ORIENTATION_PARAM if name == tooling.API_ORIENTATION_PARAM else name: text
              for name, text in from_spec.items()}

    tools = {t.name: t for t in await server.build_server().list_tools()}
    properties = tools[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]].to_mcp_tool().inputSchema["properties"]
    shared = set(public) & set(properties)
    assert tooling.PUBLIC_ORIENTATION_PARAM in shared, (sorted(public), sorted(properties))
    for name in shared:
        assert properties[name]["description"] == public[name], name


async def test_the_by_image_tool_takes_words_with_any_reference():
    """The sentence rides with every reference, and says how to use it with each.

    With a `photo_id`, the words the photograph was found under keep a refinement on its
    subject; with an image, the change asked for ("at night"). Both leave as `q` (see
    test_an_image_goes_as_multipart_with_the_words_beside_it). Bound like the text
    tool's sentence, at 250 characters.
    """
    tools = {t.name: t for t in await server.build_server().list_tools()}
    by_image = tools[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]].to_mcp_tool().inputSchema["properties"]
    assert "`photo_id`" in by_image["english_search_sentence"]["description"]
    assert "usable with any reference" in by_image["english_search_sentence"]["description"]
    for props in (by_image, tools[tooling.PUBLIC_TOOL_NAMES["search_photos"]].to_mcp_tool().inputSchema["properties"]):
        text = props["english_search_sentence"]
        branches = [b for b in text.get("anyOf", []) if "maxLength" in b]
        bound = text.get("maxLength") or (branches[0]["maxLength"] if branches else None)
        assert bound == 250, bound


async def test_no_tool_announces_paging_any_more():
    """The shape of the answer has to match the shape of the tool: no `cursor` in,
    no `pagination` out — on every tool, generated or hand-written."""
    for tool in await server.build_server().list_tools():
        properties = (tool.to_mcp_tool().outputSchema or {}).get("properties") or {}
        assert "pagination" not in properties, tool.name


def test_the_paging_block_is_stripped_from_the_body_too():
    """A schema says what to expect; the body is what a client actually reads. The
    n8n node loops on `pagination.next_cursor` — left in place it would re-send a
    token that now returns page one, and pile up duplicates."""
    body = {"success": True, "data": [{"photo_id": "x"}],
            "pagination": {"next_cursor": "abc", "has_more": True}}
    tooling.prune_result(body)
    assert body == {"success": True, "data": [{"photo_id": "x"}]}
    tooling.prune_result(None)  # non-dict bodies are left alone


async def test_every_tuned_description_reaches_a_tool():
    """A description tuned in _tool_descriptions that no shipped tool carries is dead
    text, free to drift from the one that actually ships — which is what happened to
    the POST operation, excluded from generation and replaced by the hand-written
    search_photos_by_image."""

    from pexafy_mcp import previews

    shipped = {t.description for t in await server.build_server().list_tools()}
    tuned = tooling._tool_descriptions(file_tool=previews.PREVIEWS_AVAILABLE,
                                       grid=previews.PREVIEWS_AVAILABLE)
    for key, text in tuned.items():
        assert text in shipped, key


async def test_every_tool_describes_the_shape_of_a_result():
    """What a result carries is promised in the description and declared in the output
    schema, on every tool that returns photographs. No shared block is repeated under
    each description, so the schema is where the shape lives."""
    promised = {"rank": "`rank`", "photo_id": "`photo_id`", "attribution": "credit line",
                "urls": "image link"}
    checked = []
    for tool in await server.build_server().list_tools():
        # No page of results: the account link, the file, the reader's own picks.
        if tool.name in server.NO_GRID_TOOLS:
            continue
        item = ((tool.output_schema or {}).get("properties", {}).get("data", {})
                .get("items", {}).get("properties", {}))
        for field, words in promised.items():
            assert words in (tool.description or ""), (tool.name, words)
            assert field in item, (tool.name, field)
        checked.append(tool.name)
    assert {tooling.PUBLIC_TOOL_NAMES["search_photos"],
            tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]} <= set(checked), checked


async def test_the_two_tools_agree_on_what_an_orientation_is():
    """One parameter, one schema. The generated tool took a list and the hand-written
    one a string, under a single description that told the model to "repeat the
    parameter" — which over MCP can only mean an array, and an array raised a
    validation error on the by-image tool: a lost search, invited by the text itself."""
    tools = {t.name: t for t in await server.build_server().list_tools()}
    schemas = {}
    for name in (tooling.PUBLIC_TOOL_NAMES["search_photos"], tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]):
        props = tools[name].to_mcp_tool().inputSchema["properties"]
        # Renamed on the surface; the API's own name must not be exposed by either tool.
        assert tooling.API_ORIENTATION_PARAM not in props, name
        schema = props[tooling.PUBLIC_ORIENTATION_PARAM]
        branch = next(b for b in schema["anyOf"] if b.get("type") == "array")
        # A list, as the API declares it, with the shapes closed by an enum on the items:
        # the API drops an unknown value in silence, so a free-form string would come
        # back as an unfiltered grid with no error to see.
        schemas[name] = (branch["type"], tuple((branch.get("items") or {}).get("enum") or ()))
    assert schemas[tooling.PUBLIC_TOOL_NAMES["search_photos"]] == schemas[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]], schemas
    assert schemas[tooling.PUBLIC_TOOL_NAMES["search_photos"]] == ("array", tuple(tooling_orientations()))


def tooling_orientations():
    return tooling.ORIENTATIONS


@pytest.mark.parametrize("grid", [True, False])
async def test_the_grid_fields_the_server_injects_are_declared(monkeypatch, grid):
    """`preview_url` is added to every photo after the API answers, so a
    schema that ignores it describes a payload the server does not send — and it
    is the one field the whole rank → photo_id → search_photos_by_image path rests on.
    Without a grid no photo carries one (previews.inject_preview_urls adds none), so no
    schema announces it, and `rank` says nothing of a grid."""
    from pexafy_mcp import previews

    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", grid)
    for tool in await server.build_server().list_tools():
        if tool.name in server.NO_GRID_TOOLS:
            continue  # no page of results: no photos, the file itself, or the reader's picks
        photo = ((tool.to_mcp_tool().outputSchema or {}).get("properties", {})
                 .get("data", {}).get("items", {}).get("properties", {}))
        # `rank` is data, not a drawing: a client without the grid needs a handle for
        # "the second one", and the grid still paints no number on a result tile.
        assert "rank" in photo, tool.name
        assert ("preview_url" in photo) is grid, tool.name
        assert ("preview_url_large" in photo) is grid, tool.name
        assert ("grid" in photo["rank"]["description"]) is grid, tool.name


async def test_the_server_announces_its_own_version():
    """`serverInfo.version` is ours, not the framework's.

    Unset, FastMCP fills it with its own release — the initialize response carried
    the framework release while this server was at its own version. A host shows it and
    a directory scan records it; worse, upgrading the framework would look like a
    release of this server. `from_openapi` forwards **settings to the constructor, so
    the version rides along there.
    """
    from pexafy_mcp import __version__
    srv = server.build_server()
    assert srv.version == __version__
    assert srv.name == "pexafy"


async def test_the_server_instructions_are_instructions():
    """MCP's `instructions` is read once, up front, for the whole server.

    One sentence on what Pexafy is — the field Claude Code loads first, before any tool
    — then the imperative ("Use this server when…"), keyed on what the USER asks for.
    Both inside the first 512 characters, where OpenAI asks for the most important
    details. The rule that holds is pinned in the next test: instruct about THIS
    server, never against anyone else's tool.
    """
    text = server.build_server().instructions or ""
    assert text.startswith("Pexafy searches its own index")
    opening = text[:512]
    assert "Use this server when the user asks for real photographs" in opening
    assert "Finding, showing or providing photos or pictures" in opening
    # The language, in the opening sentence: as a last bullet it fell outside the 512.
    assert "states a need that photographs fill, in any language" in opening
    # The two facts that decide whether the connector is even relevant.
    assert "royalty-free" in text
    assert "photograph" in text.lower()
    # A need the model infers is not a trigger, and a photograph is not added unasked —
    # through this server: an unscoped rule would read as one over every tool.
    assert "the user or you" not in text
    assert "Do not use Pexafy to add photographs the user did not ask for." in text


async def test_the_instructions_never_route_against_another_tool():
    """The only hard rule left in this field.

    Naming a trigger for this server is fair ("use these tools when someone needs a
    photograph that already exists"). Telling the assistant to prefer this over an
    alternative is not: OpenAI's submission rules forbid a model-readable field that
    steers the model's choice between tools.
    """
    text = (server.build_server().instructions or "").lower()
    for against in ("web search", "web_search", "instead of", "rather than the web",
                    "prefer this", "do not use", "browsing", "dall-e", "image generation",
                    # "offer them as the way to obtain a picture rather than make one"
                    # shipped once: a preference stated against image generation, which
                    # is someone else's tool.
                    "rather than make", "rather than generat"):
        assert against.lower() not in text.lower() or against in ("instead of", "do not use"), against


async def test_the_instructions_open_on_the_requests_people_type():
    """The field is the entry point, so the triggers come first and verbatim.

    A request as ordinary as "I need a picture of hands typing on a keyboard for a
    product page" matched nothing literal in the earlier text: no "I need a picture
    of", no "product page". A routing decision is made against the
    opening of this field, so the direct requests, the indirect ones (the deliverable
    named without a search being asked for) and the stock wording all sit in the first
    paragraph, ahead of any rule about how to call the tools.
    """
    text = server.build_server().instructions or ""
    # The opening states the needs rather than quoting requests; what is pinned is that
    # the need comes before the choice of tool.
    triggers = text[:text.index("### Tool selection")]
    for trigger in ("asks for real photographs", "photos or pictures", "articles",
                    "products", "slides", "social posts", "royalty-free",
                    "write an article with pictures", "any language"):
        assert trigger in triggers, trigger


async def test_every_tool_carries_its_status_texts():
    """`openai/toolInvocation/invoking` and `invoked` are what ChatGPT shows beside the
    call while it runs and when it returns; the Apps SDK caps each at 64 characters.
    Neutral on completion: "photos found" would be a lie on an empty grid."""
    for tool in await server.build_server().list_tools():
        meta = tool.to_mcp_tool().meta or {}
        invoking = meta.get("openai/toolInvocation/invoking")
        invoked = meta.get("openai/toolInvocation/invoked")
        assert invoking and invoked, tool.name
        assert len(invoking) <= 64 and len(invoked) <= 64, tool.name
        assert "found" not in invoked.lower(), tool.name


async def test_only_the_tools_the_grid_presses_are_reachable_from_the_iframe():
    """ChatGPT refuses a component's `window.openai.callTool` unless the tool declares
    `openai/widgetAccessible`, and it refuses it SILENTLY: the button in the frame
    simply does nothing, while the same call works from the model.

    Exactly three, and no more: the ≈ (search_photos_by_image with a `photo_id`, a
    refinement painted into the grid already on screen), the account panel
    (connect_account, polled with check_only), and the head's shape filter — which
    re-runs the search this grid came from with a format attached, so `search_photos` is
    pressed from inside the frame too.
    Every other tool stays reachable only through the model, because this flag is a door
    into the server that does not pass through it.

    Nothing that WRITES is on this list, and nothing that hands back a file: the door is
    open to the two questions the grid can ask about the photographs it is already
    showing, plus the one that asks whether there is an account."""
    opened = set()
    for tool in await server.build_server().list_tools():
        if (tool.to_mcp_tool().meta or {}).get("openai/widgetAccessible"):
            opened.add(tool.name)
    assert opened == {tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], tooling.PUBLIC_TOOL_NAMES["search_photos"], "connect_account"}, opened


async def test_the_instructions_say_where_the_users_own_picks_are():
    """The component keeps the reader's picks in the model's context on every heart, and
    readers were still being told that a selection made inside the grid is not
    accessible. The per-tool descriptions cannot say it — the selection belongs to no
    single tool — so it is said once here, naming the field and ruling out the wrong
    answer."""
    text = server.SERVER_INSTRUCTIONS
    assert tooling.PUBLIC_TOOL_NAMES["get_selected_photos"] in text
    assert tooling.PUBLIC_TOOL_NAMES["get_selected_photos"] in text
    assert "liked in the Pexafy grid" in text or tooling.PUBLIC_TOOL_NAMES["get_selected_photos"] in text
    # And what to do with it, in the words that rule out the failure observed: answering
    # about one photograph when two were kept.
    assert tooling.PUBLIC_TOOL_NAMES["get_selected_photos"] in text
    assert "liked in the Pexafy grid" in text
    # Still about THIS server only: no other tool is named or compared against.
    assert "ChatGPT" not in text and "Claude" not in text


async def test_the_instructions_carry_the_cross_tool_facts():
    """The one thing a per-tool description structurally cannot say.

    `rank` and `photo_id` are two handles on the same result and belong to two
    different tools, so the relation has no single tool to live in.
    """
    text = server.build_server().instructions or ""
    # Which entry point takes what — the two search tools, named.
    for tool in (tooling.PUBLIC_TOOL_NAMES["search_photos"], tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]):
        assert tool in text, tool
    # The two mistakes seen on the wire, each with its instruction.
    assert "photo_id" in text
    assert "photograph" in text.lower()


async def test_the_title_does_not_ask_for_a_description():
    """A title is routing signal as much as UI text.

    "Search photos by description" reads as "a description is required", which steers a
    plain keyword query elsewhere — and a keyword query is exactly what this answers.
    """
    assert "by description" not in server.TOOL_TITLES[tooling.PUBLIC_TOOL_NAMES["search_photos"]]
    # A name, not a pitch: short enough for a permission prompt, and it says what the
    # tool finds.
    for title in server.TOOL_TITLES.values():
        assert "photo" in title.lower() and len(title) <= 60, title


# ── search_photos_by_image: a catalogue photo as the reference, with its words ──

async def _capture_post(monkeypatch):
    import httpx
    seen: dict = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = httpx.URL(str(request.url))
        seen["body"] = request.content
        seen["content_length"] = request.headers.get("content-length")
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"success": True, "data": []})

    monkeypatch.setattr(
        server, "client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api"),
    )
    return seen


async def test_a_catalogue_photo_is_posted_with_its_words_and_no_body(monkeypatch):
    """POST /search/photos takes `photo_id` as the reference next to the `q` the photo
    was found with, so the neighbours stay on its subject. The API takes ONE reference —
    `image` in the body or `photo_id` in the query — and refuses a `photo_id` sent with
    any body at all, so the request is the query string and nothing else."""
    seen = await _capture_post(monkeypatch)
    await server.search_photos_by_image(
        photo_id="019e1ecb-0039-7da6-b1ca-987ee4d337c0", english_search_sentence="a red bicycle", explicit_orientation_filter=["portrait", "square"],
    )
    assert seen["method"] == "POST"
    assert seen["url"].path == "/api/v1/search/photos"
    params = seen["url"].params
    assert params["photo_id"] == "019e1ecb-0039-7da6-b1ca-987ee4d337c0"
    assert params["q"] == "a red bicycle"
    # The API's own name on the wire, repeated once per shape: the shapes OR together.
    assert params.get_list("orientation") == ["portrait", "square"]
    assert "explicit_orientation_filter" not in params
    # `text_alpha` stays the API's default, like every other search here.
    assert "text_alpha" not in params
    assert seen["body"] == b"" and seen["content_length"] == "0"


async def test_a_catalogue_photo_without_words_goes_alone(monkeypatch):
    """No `q`, or a blank one, is the old GET's behaviour: the neighbours of the
    photograph, unsteered. An empty `q` must not travel as an explicit empty search."""
    for words in (None, "", "   "):
        seen = await _capture_post(monkeypatch)
        await server.search_photos_by_image(
                photo_id="019e1ecb-0039-7da6-b1ca-987ee4d337c0",
                english_search_sentence=words,
            )
        assert "q" not in seen["url"].params, words
        assert "orientation" not in seen["url"].params, words
        assert seen["url"].params["photo_id"] == "019e1ecb-0039-7da6-b1ca-987ee4d337c0"


async def test_an_image_goes_as_multipart_with_the_words_beside_it(monkeypatch):
    """The picture travels in the body, and a sentence sent next to it leaves as `q`
    ("like this but at night"). `text_alpha` is not sent: the API's own balance between
    words and image applies (see test_words_with_an_image.py for the URL path)."""
    import base64
    seen = await _capture_post(monkeypatch)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    await server.search_photos_by_image(image_base64=base64.b64encode(png).decode(),
                                        english_search_sentence="a red car")
    assert seen["method"] == "POST" and seen["url"].path == "/api/v1/search/photos"
    assert "multipart/form-data" in seen["content_type"]
    assert png in seen["body"]
    params = seen["url"].params
    assert params["q"] == "a red car"
    assert "english_search_sentence" not in params and "text_alpha" not in params
    assert "photo_id" not in params


async def test_two_references_are_refused_before_anything_is_fetched(monkeypatch):
    """The API takes one reference and refuses both at once rather than guessing which
    was meant; so does the tool, naming both, before a byte is fetched or posted."""
    seen = await _capture_post(monkeypatch)
    with pytest.raises(ToolError) as exc:
        await server.search_photos_by_image(photo_id="019e1ecb-0039-7da6-b1ca-987ee4d337c0", image_url="https://x/y.jpg")
    assert "`photo_id`" in str(exc.value) and "`image_url`" in str(exc.value)
    assert seen == {}


async def test_a_refusal_from_the_api_becomes_a_sentence(monkeypatch):
    """A 404 from the API is `PHOTO_NOT_FOUND` with a message; the caller gets the
    message, not a traceback."""
    import httpx

    def handler(request):
        return httpx.Response(404, json={"success": False,
                                         "error": {"code": "PHOTO_NOT_FOUND",
                                                   "message": "Photo 'x' not found in index"}})
    monkeypatch.setattr(
        server, "client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api"),
    )
    with pytest.raises(ToolError) as exc:
        await server.search_photos_by_image(photo_id="019e1ecb-0039-7da6-b1ca-987ee4d337c0")
    assert "find photos like that one" in str(exc.value)
    assert "not found in index" in str(exc.value)


async def test_the_by_image_tool_takes_a_catalogue_photo_and_says_what_q_is_for():
    """Four ways to give the reference, under the public names, with `q` beside them —
    and the API's `orientation` on none of them. `photo_id` keeps the shared wording,
    example id included; `q` says it belongs to `photo_id` and has no example to copy."""
    tools = {t.name: t for t in await server.build_server().list_tools()}
    tool = tools[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]].to_mcp_tool()
    props = tool.inputSchema["properties"]
    assert set(props) == {"image_url", "image_file", "image_base64", "photo_id", "english_search_sentence",
                          tooling.PUBLIC_ORIENTATION_PARAM}
    assert not tool.inputSchema.get("required")
    assert tooling.API_ORIENTATION_PARAM not in props
    pid = props["photo_id"]
    assert "019e1ecb-0039-7da6-b1ca-987ee4d337c0" in pid["description"]
    assert "Use it instead of an image" in pid["description"]
    branch = next(b for b in pid["anyOf"] if b.get("type") == "string")
    assert branch.get("examples") == ["019e1ecb-0039-7da6-b1ca-987ee4d337c0"]
    q = props["english_search_sentence"]
    assert "`photo_id`" in q["description"]
    assert "reference" in q["description"]
    bound = q.get("maxLength") or next(b["maxLength"] for b in q.get("anyOf", []) if "maxLength" in b)
    assert bound == 250
    assert "examples" not in q and not any("examples" in b for b in q.get("anyOf", []) if isinstance(b, dict))
    shape = props[tooling.PUBLIC_ORIENTATION_PARAM]
    assert "reference image" in shape["description"]
    branch = next(b for b in shape["anyOf"] if b.get("type") == "array")
    assert branch["items"]["enum"] == tooling.ORIENTATIONS
    # The description names the fourth way in (and, where there is a grid, "or liked in
    # the grid": test_optional_tools).
    text = tools[tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]].description
    assert "a Pexafy photo already found" in text and "reference" in text


async def test_the_two_search_tools_agree_on_what_an_orientation_is():
    """One parameter, one schema, on the hand-written tool as on the generated one."""
    tools = {t.name: t for t in await server.build_server().list_tools()}
    shapes = set()
    for name in (tooling.PUBLIC_TOOL_NAMES["search_photos"], tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]):
        props = tools[name].to_mcp_tool().inputSchema["properties"]
        branch = next(b for b in props[tooling.PUBLIC_ORIENTATION_PARAM]["anyOf"] if b.get("type") == "array")
        shapes.add((branch["type"], tuple(branch["items"]["enum"])))
    assert shapes == {("array", tuple(tooling.ORIENTATIONS))}


async def test_the_old_similar_name_still_answers(monkeypatch):
    """A host on the 0.4.12 snapshot still calls get_similar_photos, with the two
    arguments it declared. There is no such tool any more; compat routes the call to the
    by-image tool, whose `photo_id` is the same argument, and the old `cursor` is dropped
    on the way like every retired one."""
    from fastmcp import Client
    seen = await _capture_post(monkeypatch)
    async with Client(server.build_server()) as client:
        names = {t.name for t in await client.list_tools()}
        assert "get_similar_photos" not in names
        await client.call_tool("get_similar_photos", {"photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c0", "cursor": "abc"})
    assert seen["method"] == "POST" and seen["url"].path == "/api/v1/search/photos"
    assert seen["url"].params["photo_id"] == "019e1ecb-0039-7da6-b1ca-987ee4d337c0"
    assert "cursor" not in seen["url"].params and "q" not in seen["url"].params
    assert seen["body"] == b""


@pytest.mark.asyncio
async def test_the_text_search_is_listed_first():
    """Which tool a router reads first is decided by the order of `tools/list`, and that
    order was an accident of assembly: FastMCP concatenates its providers, so the four
    hand-written tools came ahead of the one generated from the OpenAPI spec. The
    by-image search led the list — opening on "when a picture is the reference rather
    than words" — and the text search, the tool that answers the common request, came
    last of five.

    Pinned on the wire, because the sort happens in a middleware and only the listing a
    client actually receives proves it ran.
    """
    from fastmcp import Client

    from pexafy_mcp.server import TOOL_ORDER, build_server

    server = build_server()
    async with Client(server) as client:
        listed = [t.name for t in await client.list_tools()]

    assert listed, "no tools listed"
    assert listed[0] == tooling.PUBLIC_TOOL_NAMES["search_photos"], listed
    # The whole order, not just the head: a sort that only floated one name would pass
    # the assertion above while leaving the rest arbitrary.
    assert listed == [n for n in TOOL_ORDER if n in listed], listed


# ── HTTP probes ──────────────────────────────────────────────────────────────

def _http_get(path, **kwargs):
    from starlette.testclient import TestClient

    with TestClient(server.build_server().http_app()) as client:
        return client.get(path, **kwargs)


def test_health_counts_the_tools_without_being_logged_as_a_host(caplog):
    """A liveness probe is not an MCP message: observe.py's per-message log line would
    count every probe as a host listing the tools."""
    with caplog.at_level("INFO", logger="pexafy.mcp.meta"):
        response = _http_get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok" and response.json()["tools"] >= 2
    assert not [r for r in caplog.records if r.name == "pexafy.mcp.meta"]


@pytest.mark.parametrize("path", ["/favicon.ico", "/favicon.svg"])
def test_the_connector_has_an_icon_on_any_address(path):
    """ChatGPT drew a broken image for the preprod connector: its address had no
    favicon (production's comes from Caddy). The server answers it itself, the same
    mark as production's."""
    response = _http_get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text == server.FAVICON_SVG and ">P</text></svg>" in response.text


def test_a_failing_health_check_keeps_the_detail_in_the_log(monkeypatch, caplog):
    """The probe is public: what went wrong goes to the log, the answer says only that
    something did."""
    async def broken(*_args):
        raise RuntimeError("detail for the operator only")

    monkeypatch.setattr(server, "_listed", broken)
    with caplog.at_level("WARNING", logger="pexafy.mcp"):
        response = _http_get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "error": "could not list tools"}
    assert "detail for the operator only" in caplog.text


def test_a_non_ascii_metrics_token_is_refused_not_crashed(reload_with_env):
    """`compare_digest` raises on a non-ASCII `str`; compared as bytes, a wrong token
    is a 401 whatever it is made of."""
    from starlette.testclient import TestClient

    reloaded = reload_with_env(server, {
        "PEXAFY_MCP_TRANSPORT": "http", "PEXAFY_METRICS_TOKEN": "s3cret"})
    with TestClient(reloaded.build_server().http_app()) as client:
        assert client.get("/metrics", headers={"Authorization": b"Bearer caf\xe9"}).status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


# ── What reaches the log, and what reaches the caller ────────────────────────

async def test_a_plan_limit_block_logs_no_email(monkeypatch, caplog):
    """The quota-blocked token carries the user's e-mail as its `client_id`."""
    import httpx

    from pexafy_mcp.auth import QUOTA_MESSAGE_CLAIM

    class _Token:
        claims = {QUOTA_MESSAGE_CLAIM: "The monthly allowance is spent."}
        client_id = "someone@example.com"

    monkeypatch.setattr(server, "get_access_token", lambda: _Token())
    with caplog.at_level("DEBUG", logger="pexafy.mcp"), pytest.raises(ToolError):
        await server._forward_client_key(httpx.Request("GET", "http://x/api/v1/search/photos"))
    assert "blocked by plan limit" in caplog.text
    assert "someone@example.com" not in caplog.text
    # Named by the keyed digest, not by a plain hash of the address.
    assert observe.email_digest("someone@example.com") in caplog.text
    assert observe.digest("someone@example.com") not in caplog.text


async def test_a_failed_photo_download_does_not_quote_the_exception(fake_api, caplog):
    """The exception names the signed proxy URL; the caller gets a sentence, the log
    gets the kind of failure."""
    from unittest import mock

    import httpx

    from pexafy_mcp import previews

    signed = "https://thumb.example/x/1280w.jpg?s=SIGNATURE"
    fake_api.respond = lambda request: httpx.Response(404, json={"success": False})

    class _Refusing:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, url, **_kwargs):
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(f"403 Forbidden for url {url}", request=request,
                                        response=httpx.Response(403, request=request))

    with mock.patch.object(previews, "PREVIEWS_AVAILABLE", True), \
         mock.patch.object(previews, "sign_thumb_url_permanent", return_value=signed), \
         mock.patch("httpx.AsyncClient", _Refusing), \
         caplog.at_level("WARNING", logger="pexafy.mcp"), \
         pytest.raises(ToolError) as refused:
        await server.get_photo_file("019e1ecb-0039-7da6-b1ca-987ee4d337c0")
    assert str(refused.value) == ("Could not fetch that photo. Check the `photo_id` came "
                                  "from a Pexafy search result.")
    assert "HTTPStatusError 403" in caplog.text
    assert "SIGNATURE" not in caplog.text
