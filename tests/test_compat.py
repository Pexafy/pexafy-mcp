"""A call made against an older published surface is answered, not rejected.

Old tool names and renamed parameters are translated, retired filters are dropped. The
window this covers is real: a directory serves a snapshot of the tool schema taken at
review time, so between a deploy and the new snapshot's approval, hosts keep offering
what the old one declared and assistants keep sending it.
"""
from __future__ import annotations

import base64
import copy

import httpx
import jsonschema
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Implementation

from pexafy_mcp import compat, previews, server, tooling

PHOTO_ID = "019e1ecb-0039-7da6-b1ca-987ee4d337c0"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Message:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Context:
    def __init__(self, message):
        self.message = message


async def _passthrough(context):
    return None


async def test_retired_arguments_are_stripped_before_the_tool_runs():
    seen = {}

    async def call_next(context):
        seen.update(context.message.arguments)
        return "ok"

    message = _Message(tooling.PUBLIC_TOOL_NAMES["search_photos"], {
        "english_search_sentence": "a red car", "color_name": "red", "source": "Pexels",
        "license_type": "free", "after_date": "2025-01-01", "photographer": "nasa",
        "color_hex": "#ff0000", "color_tolerance": 20,
    })
    result = await compat.DropRetiredParams().on_call_tool(_Context(message), call_next)

    assert result == "ok"
    assert seen == {"english_search_sentence": "a red car"}, seen


async def test_orientation_is_renamed_not_retired():
    """It is the one filter 1.0.0 kept — stripping it would break a real request.

    A published snapshot still advertises it under the API's name, and the live tools
    take the new one: dropped, a stated shape would be silently ignored; left alone, the
    call would die on "unexpected keyword argument". It is translated instead, and the
    old surface's single string is widened to the list the new one takes.
    """
    from pexafy_mcp import tooling
    assert tooling.API_ORIENTATION_PARAM not in compat.RETIRED_PARAMS

    async def call_next(context):
        return context.message.arguments

    message = _Message(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "a red car", "orientation": "landscape"})
    kept = await compat.DropRetiredParams().on_call_tool(_Context(message), call_next)
    assert kept == {"english_search_sentence": "a red car", "explicit_orientation_filter": ["landscape"]}


async def test_a_caller_on_the_current_schema_wins_over_the_old_name():
    """Both names in one call: the new one was chosen against the schema that ships,
    so it is the one that meant what it said. The old name is dropped, not merged."""
    async def call_next(context):
        return context.message.arguments

    message = _Message(tooling.PUBLIC_TOOL_NAMES["search_photos"], {
        "english_search_sentence": "a red car",
        "orientation": "landscape",
        "explicit_orientation_filter": ["portrait"],
    })
    kept = await compat.DropRetiredParams().on_call_tool(_Context(message), call_next)
    assert kept == {"english_search_sentence": "a red car", "explicit_orientation_filter": ["portrait"]}


async def test_a_call_carrying_old_filters_is_not_rejected(fake_api):
    """End to end: the same call that raised "unexpected keyword argument" before
    the middleware now reaches the endpoint — without the filters."""
    async with Client(server.build_server()) as client:
        await client.call_tool(
            tooling.PUBLIC_TOOL_NAMES["search_photos"],
            {"english_search_sentence": "a red car", "color_name": "red", "license_type": "free"},
        )
    [request] = fake_api.requests
    assert request.url.params["q"] == "a red car"
    assert "color_name" not in request.url.params
    assert "license_type" not in request.url.params


@pytest.mark.parametrize("tool, reference", [
    (tooling.PUBLIC_TOOL_NAMES["search_photos"], {}),
    (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": PHOTO_ID}),
])
async def test_a_sentence_over_the_cap_is_refused_before_the_api(fake_api, tool, reference):
    """`maxLength` binds only a client that validates, so the server enforces it: past
    250 characters the call is refused with the reason and nothing is sent. A sentence
    at the cap goes through whole — never truncated."""
    async with Client(server.build_server()) as client:
        with pytest.raises(ToolError, match="capped at 250 characters and this one is 251"):
            await client.call_tool(tool, {**reference, "english_search_sentence": "x" * 251})
        assert fake_api.requests == []
        await client.call_tool(tool, {**reference, "english_search_sentence": "x" * 250})
    [request] = fake_api.requests
    assert request.url.params["q"] == "x" * 250


async def test_the_tuning_knob_is_not_the_assistants_to_turn():
    """`text_alpha` weighs the words against the image in a combined search. Asked for
    a number, an assistant picks one — so it is not offered. Unsent, the API applies
    1.7, the value the website uses when nobody touches its slider, and the MCP surface
    follows that default automatically if it ever moves."""
    tools = {t.name: t for t in await server.build_server().list_tools()}
    for name, tool in tools.items():
        assert "text_alpha" not in tool.to_mcp_tool().inputSchema.get("properties", {}), name


async def test_the_renamed_filter_still_leaves_under_the_api_name(monkeypatch):
    """The rename must stop at the tool surface.

    `explicit_orientation_filter` is what an assistant fills in; `orientation` is what
    the API understands, and it drops a query parameter it does not know without an
    error. So a rename that leaked onto the wire would not fail loudly — it would
    return an unfiltered grid to someone who asked for a portrait, forever. This
    captures the outgoing request and reads the query string itself.

    Both tools are exercised: the generated one is renamed by an ArgTransform, the
    hand-written by-image one translates the name in its own signature, and those are
    two separate places to get it wrong.
    """
    seen: list[httpx.Request] = []

    async def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"success": True, "data": [], "meta": {}},
            request=request,
        )

    monkeypatch.setattr(
        server.client, "_transport", httpx.MockTransport(capture), raising=False
    )
    async with Client(server.build_server()) as client:
        await client.call_tool(
            tooling.PUBLIC_TOOL_NAMES["search_photos"],
            {"english_search_sentence": "a red car", "explicit_orientation_filter": ["landscape", "square"]},
        )
    assert seen, "no request was captured"
    params = seen[-1].url.params
    assert "explicit_orientation_filter" not in params, seen[-1].url
    # Several shapes ride as a repeated parameter — the form the API ORs together.
    assert params.get_list("orientation") == ["landscape", "square"], seen[-1].url


# ── The 0.4.12 contract ───────────────────────────────────────────────────────
# The surface 0.4.12 published — the release in the directories, and the one every
# already-connected host still holds. Read off that release's `tools/list`, not written
# from memory: it is the contract this middleware bridges, so it is pinned here.
PUBLISHED_0_4_12 = {
    "search_photos": {
        "q", "color_name", "color_hex", "color_tolerance", "orientation", "source",
        "license_type", "cursor", "after_date", "photographer",
    },
    "search_photos_by_image": {
        "image_url", "image_file", "image_base64", "q", "orientation", "source",
        "color_name", "license_type", "photographer", "after_date", "text_alpha", "cursor",
    },
    "get_similar_photos": {"photo_id", "cursor"},
}

# A value of the type 0.4.12 declared, for each of its parameters.
SAMPLE_0_4_12 = {
    "q": "a red car", "color_name": "red", "color_hex": "#ff0000", "color_tolerance": 20,
    "orientation": "landscape", "source": "pexels", "license_type": "free", "cursor": "abc",
    "after_date": "2024-01-01", "photographer": "nasa", "text_alpha": 1.7,
    "image_url": "https://images.example/reference.jpg",
    "image_file": {"download_url": "https://files.example/upload.png", "file_id": "file_1"},
    "image_base64": base64.b64encode(PNG).decode(), "photo_id": PHOTO_ID,
}


async def test_every_parameter_the_published_snapshot_offers_is_still_accepted():
    """Nothing an already-connected host can send may raise.

    A directory serves the schema it captured at review time and routes the call to the
    live server, so for as long as 0.4.12 is the published snapshot every argument in it
    reaches 1.0.0. An argument the tool no longer declares raises "unexpected keyword
    argument" — a failed search, not a degraded one — so each one, sent through the
    compatibility middleware, must come out served, renamed to a live name, or dropped.
    """
    tools = {t.name: t for t in await server.build_server().list_tools()}
    for name, published in PUBLISHED_0_4_12.items():
        for param in sorted(published):
            message = _Message(name, {param: copy.deepcopy(SAMPLE_0_4_12[param])})
            await compat.DropRetiredParams().on_call_tool(_Context(message), _passthrough)
            assert message.name in tools, f"{name} is neither served nor routed to a tool"
            live = set(tools[message.name].to_mcp_tool().inputSchema.get("properties", {}))
            assert set(message.arguments) <= live, (
                f"{name}.{param} reaches {message.name} as {sorted(message.arguments)}, "
                "which it does not declare — a host still on 0.4.12 would get an error"
            )
            if param in live:
                assert param in message.arguments, f"{name}.{param} is live but was dropped"


# Calls as a host holding the 0.4.12 snapshot sends them, and the query string that
# must leave for the API (`per_page` aside, which the server always sets).
REPLAYED_0_4_12 = [
    ("search_photos",
     {"q": "a red car", "orientation": "landscape", "color_name": "red", "cursor": "abc"},
     {"q": ["a red car"], "orientation": ["landscape"]}),
    ("search_photos",
     {"q": "a quiet beach", "color_hex": "#ff0000", "color_tolerance": 20, "source": "pexels",
      "license_type": "free", "after_date": "2024-01-01", "photographer": "nasa"},
     {"q": ["a quiet beach"]}),
    ("search_photos_by_image",
     {"image_url": "https://images.example/reference.jpg", "q": "at night"},
     {"q": ["at night"]}),
    ("search_photos_by_image",
     {"image_file": {"download_url": "https://files.example/upload.png", "file_id": "file_1"},
      "orientation": "portrait", "text_alpha": 1.7, "source": "unsplash", "color_name": "blue",
      "license_type": "free", "photographer": "nasa", "after_date": "2024-01-01", "cursor": "abc"},
     {"orientation": ["portrait"]}),
    ("search_photos_by_image", {"image_base64": SAMPLE_0_4_12["image_base64"]}, {}),
    ("get_similar_photos", {"photo_id": PHOTO_ID}, {"photo_id": [PHOTO_ID]}),
    ("get_similar_photos", {"photo_id": PHOTO_ID, "cursor": "abc"}, {"photo_id": [PHOTO_ID]}),
]


def test_the_replay_covers_every_published_parameter():
    covered: dict[str, set] = {}
    for tool, arguments, _ in REPLAYED_0_4_12:
        covered.setdefault(tool, set()).update(arguments)
    assert covered == PUBLISHED_0_4_12


@pytest.mark.parametrize(
    "tool, arguments, wire", REPLAYED_0_4_12,
    ids=[f"{tool}-{'+'.join(arguments)}" for tool, arguments, _ in REPLAYED_0_4_12],
)
async def test_a_0_4_12_call_is_answered_end_to_end(fake_api, monkeypatch, tool, arguments, wire):
    """The whole server, in memory: the call succeeds, and the API receives the words
    as `q`, the shape as `orientation`, and none of the retired filters."""
    fetched = []

    async def fetch_image(url):
        fetched.append(url)
        return PNG, "image/png", "reference.png"

    monkeypatch.setattr(server, "_fetch_image", fetch_image)
    async with Client(server.build_server()) as client:
        result = await client.call_tool(tool, copy.deepcopy(arguments))

    assert result.structured_content["success"] is True
    [request] = fake_api.requests
    assert (request.method, request.url.path) == (
        "GET" if tool == "search_photos" else "POST", "/api/v1/search/photos")
    params = request.url.params
    assert {key: params.get_list(key) for key in params if key != "per_page"} == wire
    # A picture travels in the body; a catalogue photo is the query string alone.
    source = arguments.get("image_url") or arguments.get("image_file", {}).get("download_url")
    assert fetched == ([source] if source else [])
    if tool == "search_photos_by_image":
        assert PNG in request.content
    elif tool == "get_similar_photos":
        assert request.content == b""


# The long names the two searches wore on preprod (2026-09-25/26). A connector refreshed
# in that window still sends them, with the 1.0.0 parameters.
@pytest.mark.parametrize("old, arguments, wire", [
    ("search_photos_from_unsplash_pexels_pixabay_by_text",
     {"english_search_sentence": "a quiet beach", "explicit_orientation_filter": ["portrait"]},
     {"q": ["a quiet beach"], "orientation": ["portrait"]}),
    ("search_photos_from_unsplash_pexels_pixabay_by_image",
     {"photo_id": PHOTO_ID, "english_search_sentence": "at night"},
     {"photo_id": [PHOTO_ID], "q": ["at night"]}),
])
async def test_a_call_under_a_long_preprod_name_is_answered(fake_api, old, arguments, wire):
    async with Client(server.build_server()) as client:
        result = await client.call_tool(old, copy.deepcopy(arguments))
    assert result.structured_content["success"] is True
    [request] = fake_api.requests
    params = request.url.params
    assert {key: params.get_list(key) for key in params if key != "per_page"} == wire


async def test_a_0_4_12_search_with_filters_only_is_told_what_to_send(fake_api):
    """`q` was optional in 0.4.12 and a filter-only search was legal. It is answered
    with what to send instead, before anything leaves — not with a TypeError."""
    async with Client(server.build_server()) as client:
        with pytest.raises(ToolError, match="is required: it is the only thing"):
            await client.call_tool("search_photos", {"color_name": "red", "orientation": "landscape"})
    assert fake_api.requests == []


async def test_the_old_similar_name_is_routed_to_the_image_tool():
    """There is no similar tool any more — a catalogue photo is `photo_id` on the
    by-image tool — and a host on the 0.4.12 snapshot still calls the old name. The
    name is read off the message after the chain, so rewriting it here is the route;
    the retired `cursor` goes on the way, under the NEW name's rules."""
    seen = {}

    async def call_next(context):
        seen["name"] = context.message.name
        seen["args"] = dict(context.message.arguments)
        return "ok"

    mw = compat.DropRetiredParams()
    await mw.on_call_tool(
        _Context(_Message("get_similar_photos", {"photo_id": PHOTO_ID, "cursor": "x"})),
        call_next,
    )
    assert seen == {"name": tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], "args": {"photo_id": PHOTO_ID}}


async def test_text_alpha_is_dropped_on_the_image_tool_and_q_now_reaches_it():
    """The split that makes the fix safe: strip `q` everywhere and every text search dies.
    `q` is live again on the by-image tool — the words beside the reference — so a
    0.4.12 call keeps it, under the new name; `text_alpha` still goes."""
    seen = {}

    async def call_next(context):
        seen.clear()
        seen.update(context.message.arguments)
        return "ok"

    mw = compat.DropRetiredParams()
    await mw.on_call_tool(
        _Context(_Message("search_photos_by_image", {"image_url": "https://x/y.jpg", "q": "a red car", "text_alpha": 1.7})),
        call_next,
    )
    assert seen == {"image_url": "https://x/y.jpg", "english_search_sentence": "a red car"}, seen

    await mw.on_call_tool(_Context(_Message("search_photos", {"q": "a red car"})), call_next)
    assert seen == {"english_search_sentence": "a red car"}, seen



# ── What assistants send that no snapshot published ──────────────────────────
# Production, the 72 hours to 2026-09-17: six calls failed on "unexpected keyword
# argument" — `query` three times, `per_page` twice, `limit` once. No published definition
# ever offered them: an assistant invents them. `query` is the sentence under the name a
# REST API would give it; the two others are a page size, which the server sets itself.

async def test_query_is_the_sentence_on_both_searches(caplog):
    seen = {}

    async def call_next(context):
        seen.clear()
        seen.update(context.message.arguments)
        return "ok"

    mw = compat.DropRetiredParams()
    for tool in (tooling.PUBLIC_TOOL_NAMES["search_photos"],
                 tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]):
        with caplog.at_level("INFO", logger="pexafy.mcp"):
            await mw.on_call_tool(_Context(_Message(tool, {"query": "a red car"})), call_next)
        assert seen == {"english_search_sentence": "a red car"}, tool
        assert f"Renamed parameter on {tool}: query -> english_search_sentence" in caplog.text


async def test_a_published_name_wins_over_query():
    """Sent together, `q` — the name 0.4.12 published — or the current name wins over the
    invented one, which is dropped rather than merged."""
    async def call_next(context):
        return dict(context.message.arguments)

    mw = compat.DropRetiredParams()
    tool = tooling.PUBLIC_TOOL_NAMES["search_photos"]
    for sent in ({"q": "a red car", "query": "a blue car"},
                 {"english_search_sentence": "a red car", "query": "a blue car"}):
        kept = await mw.on_call_tool(_Context(_Message(tool, sent)), call_next)
        assert kept == {"english_search_sentence": "a red car"}, sent


async def test_a_page_size_is_ignored_like_a_retired_filter(caplog):
    """On every tool, with the line every retired parameter gets: its rate says when
    this can go."""
    async def call_next(context):
        return dict(context.message.arguments)

    mw = compat.DropRetiredParams()
    search = tooling.PUBLIC_TOOL_NAMES["search_photos"]
    photo_file = tooling.PUBLIC_TOOL_NAMES["get_photo_file"]
    with caplog.at_level("INFO", logger="pexafy.mcp"):
        kept = await mw.on_call_tool(_Context(_Message(search, {
            "english_search_sentence": "a red car", "per_page": 50, "limit": 5})), call_next)
        assert kept == {"english_search_sentence": "a red car"}
        kept = await mw.on_call_tool(_Context(_Message(photo_file, {
            "photo_id": PHOTO_ID, "limit": 1})), call_next)
        assert kept == {"photo_id": PHOTO_ID}
    assert f"Retired parameter(s) ignored on {search}: limit, per_page" in caplog.text
    assert f"Retired parameter(s) ignored on {photo_file}: limit" in caplog.text


# Calls as assistants sent them, and the query string that must leave for the API. The
# page size on the wire is the server's, whatever the call asked for.
INVENTED = [
    ("search_photos", {"query": "a red car", "per_page": 50}, {"q": ["a red car"]}),
    ("search_photos", {"q": "a quiet beach", "limit": 5, "orientation": "portrait"},
     {"q": ["a quiet beach"], "orientation": ["portrait"]}),
    ("search_photos", {"english_search_sentence": "a quiet beach", "per_page": 30, "limit": 30},
     {"q": ["a quiet beach"]}),
    ("search_photos_by_image", {"photo_id": PHOTO_ID, "query": "at night", "limit": 5},
     {"photo_id": [PHOTO_ID], "q": ["at night"]}),
    ("get_similar_photos", {"photo_id": PHOTO_ID, "per_page": 30}, {"photo_id": [PHOTO_ID]}),
]


@pytest.mark.parametrize(
    "tool, arguments, wire", INVENTED,
    ids=[f"{tool}-{'+'.join(arguments)}" for tool, arguments, _ in INVENTED],
)
async def test_a_call_with_invented_parameters_is_answered_end_to_end(fake_api, tool, arguments, wire):
    async with Client(server.build_server()) as client:
        result = await client.call_tool(tool, copy.deepcopy(arguments))
    assert result.structured_content["success"] is True
    [request] = fake_api.requests
    params = request.url.params
    assert params["per_page"] == str(server.GRID_PAGE_SIZE)
    assert {key: params.get_list(key) for key in params if key != "per_page"} == wire


# ── `get_similar_photos`, listed to no host ───────────────────────────────────
# 1.0.0 kept it in the list an OpenAI host reads, as 0.4.12 defined it, until the
# published app's 1.0.0 tools were live (`photo_id` on the by-image search among them).
# They are, and the alias went. A call under the old name is still the by-image search.

# Every host, OpenAI's included, and a client that names itself as nothing known.
HOSTS = ("openai-mcp", "ChatGPT", "Anthropic/ClaudeAI", "claude-ai", "Anthropic",
         "claude-code", "Visual Studio Code", "cursor-vscode", "goose", "mcp-scan", None)

# Two photographs as the API writes them, every field 0.4.12 declared in its output.
API_PHOTOS = [
    {
        "photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c1",
        "image_url": "https://images.example/1/full.jpg",
        "urls": {size: f"https://images.example/1/{size}.jpg"
                 for size in ("thumb", "small", "regular", "large", "full")},
        "width": 4000, "height": 3000, "blur_hash": "LKO2?U%2Tw=w]~RBVZRi};RPxuwH",
        "orientation": "landscape", "color_name": "red", "color_hex": "#aa2211",
        "photographer_username": "jdoe", "photographer_full_name": "Jane Doe",
        "photographer_url": "https://www.pexels.com/@jdoe", "source": "Pexels",
        "license_type": "free", "source_image_url": "https://www.pexels.com/photo/1/",
        "source_description": "A red bicycle", "description": "A red bicycle against a wall.",
        "alt_description": "red bicycle against a white wall", "uploaded_on": "2024-05-01",
        "relevance_score": 0.83,
        "attribution": {"html": 'Photo by <a href="https://www.pexels.com/@jdoe">Jane Doe</a> on Pexels',
                        "plain": "Photo by Jane Doe on Pexels"},
    },
    {
        "photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c2",
        "image_url": "https://images.example/2/full.jpg",
        "urls": {size: f"https://images.example/2/{size}.jpg"
                 for size in ("thumb", "small", "regular", "large", "full")},
        "width": 3000, "height": 4500, "blur_hash": None,
        "orientation": "portrait", "color_name": "white", "color_hex": "#f0f0f0",
        "photographer_username": "3345557", "photographer_full_name": None,
        "photographer_url": None, "source": "Pixabay",
        "license_type": "free", "source_image_url": None,
        "source_description": None, "description": None,
        "alt_description": None, "uploaded_on": None, "relevance_score": None,
        "attribution": {"html": "Photo by 3345557 on Pixabay", "plain": "Photo by 3345557 on Pixabay"},
    },
]


def _similar_answer(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "success": True, "data": copy.deepcopy(API_PHOTOS),
        "meta": {"request_id": "6ecfa8dc-0000-4000-8000-000000000000", "took_ms": 12.5},
        "pagination": {"next_cursor": None, "per_page": 16, "has_more": False},
        "error": None,
    })


@pytest.fixture
def grid_on(reload_with_env):
    """Production's configuration: the thumbnail proxy, hence the grid."""
    reload_with_env(previews, {
        "PEXAFY_THUMB_BASE_URL": "https://thumb.pexafy.com",
        "PEXAFY_THUMB_HMAC_SECRET": "test-secret",
    })


async def _listed_to(client_name: str | None) -> list:
    info = Implementation(name=client_name, version="1") if client_name else None
    async with Client(server.build_server(), client_info=info) as client:
        return await client.list_tools()


async def test_no_host_is_listed_it(grid_on):
    """Not ChatGPT, not Claude, not an editor, not a client that cannot be placed — and
    neither of the probes, which count and show the list those hosts read."""
    expected = [t.name for t in await _listed_to(None)]
    assert compat.SIMILAR_TOOL not in expected
    assert expected == [n for n in server.TOOL_ORDER if n in expected], expected
    for name in HOSTS:
        assert [t.name for t in await _listed_to(name)] == expected, name
    from starlette.testclient import TestClient

    with TestClient(server.build_server().http_app()) as http:
        assert http.get("/health").json()["tools"] == len(expected)
        card = http.get("/.well-known/mcp/server-card.json").json()
    assert [t["name"] for t in card["tools"]] == expected


async def test_a_call_under_the_old_name_is_a_search_by_image(grid_on, fake_api, caplog):
    """`get_similar_photos{photo_id, cursor}`, as the 0.4.12 app sent it: the by-image
    search answers, with the same photographs and the same grid as a call made under its
    own name, and an answer valid against the by-image search's own output schema."""
    fake_api.respond = _similar_answer
    reference = API_PHOTOS[0]["photo_id"]
    by_image = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
    assert compat.RENAMED_TOOLS[compat.SIMILAR_TOOL] == by_image
    async with Client(server.build_server(),
                      client_info=Implementation(name="openai-mcp", version="1.0.0")) as client:
        listed = {t.name: t for t in await client.list_tools()}
        with caplog.at_level("INFO", logger="pexafy.mcp"):
            similar = await client.call_tool(compat.SIMILAR_TOOL,
                                             {"photo_id": reference, "cursor": "abc"})
        direct = await client.call_tool(by_image, {"photo_id": reference})

    answer = similar.structured_content
    assert answer["success"] is True
    assert [p["photo_id"] for p in answer["data"]] == [p["photo_id"] for p in API_PHOTOS]
    jsonschema.validate(answer, listed[by_image].outputSchema)
    assert answer == direct.structured_content
    assert previews.PREVIEW_META_KEY in similar.meta
    assert set(similar.meta) == set(direct.meta)

    # One request each, the same: the catalogue photo as the reference, no page, no body.
    assert len(fake_api.requests) == 2
    for request in fake_api.requests:
        assert (request.method, request.url.path) == ("POST", "/api/v1/search/photos")
        assert request.url.params["photo_id"] == reference
        assert "cursor" not in request.url.params
        assert request.content == b""
    assert "Renamed tool call: get_similar_photos -> search_photos_by_image" in caplog.text
    assert "Retired parameter(s) ignored on search_photos_by_image: cursor" in caplog.text
