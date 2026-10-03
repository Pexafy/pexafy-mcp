"""Where the person sees the grid, the model reads the photographs without their links.

Seen in Cursor (2026-10-01): under the grid, the model showed the photographs again,
one picture after another — the answer it reads carried every image link, and the
served texts called `urls.regular` "the link to hand the person". In a host that shows
the grid (hosts.renders_grid) the text the model reads now names the photographs by
`photo_id`, description, credit and licence, and the file tool returns one with its
link when it is asked for. The grid draws from `_meta`, whole; `structuredContent`,
which some hosts hand the model too, loses `urls` and keeps its previews as a path
without address (2026-10-02), and the selection is read without `image_url`. Every
other client — Claude Code, a terminal, an unknown one — reads the whole answer as
before.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastmcp.apps import UI_EXTENSION_ID, UI_MIME_TYPE

from pexafy_mcp import hosts, tooling


class _Caps:
    def __init__(self, extensions=None, experimental=None):
        self.model_extra = {"extensions": extensions} if extensions is not None else {}
        self.experimental = experimental


class _Ctx:
    """A middleware context whose session says who the client is, and what it shows."""

    def __init__(self, name, caps=None):
        info = type("I", (), {"name": name})()
        params = type("P", (), {"clientInfo": info, "capabilities": caps or _Caps()})()
        session = type("S", (), {"client_params": params})()
        request = type("R", (), {"meta": type("M", (), {"model_extra": {}})()})()
        self.fastmcp_context = type("C", (), {"session": session, "request_context": request})()


@pytest.mark.parametrize("name, shows", [
    ("openai-mcp", True), ("Anthropic/ClaudeAI", True), ("claude-ai", True),
    ("Visual Studio Code", True), ("cursor-vscode", True), ("goose", True),
    ("mcpjam-inspector", True),
    # A terminal names itself Claude too, and shows no grid.
    ("claude-code", False), ("mcp", False), ("some-inspector", False), ("", False),
])
def test_the_hosts_that_show_the_grid_by_name(name, shows):
    assert hosts.renders_grid(_Ctx(name)) is shows


def test_a_client_that_says_it_shows_mcp_apps_is_one():
    ext = {UI_EXTENSION_ID: {"mimeTypes": [UI_MIME_TYPE]}}
    assert hosts.declares_apps(_Ctx("mystery", _Caps(extensions=ext)))
    assert hosts.renders_grid(_Ctx("mystery", _Caps(extensions=ext)))
    assert hosts.renders_grid(_Ctx("mystery", _Caps(extensions={UI_EXTENSION_ID: {}})))
    assert hosts.renders_grid(_Ctx("mystery", _Caps(experimental={UI_EXTENSION_ID: {}})))
    # Another MIME type is not the grid's.
    other = {UI_EXTENSION_ID: {"mimeTypes": ["text/html"]}}
    assert not hosts.renders_grid(_Ctx("mystery", _Caps(extensions=other)))
    # …and a terminal stays one whatever it declares.
    assert not hosts.renders_grid(_Ctx("claude-code", _Caps(extensions=ext)))
    assert not hosts.declares_apps(None)


@pytest.mark.parametrize("name, assistant", [
    ("openai-mcp", "ChatGPT"), ("claude-ai", "Claude"), ("Anthropic/ClaudeAI", "Claude"),
    ("Visual Studio Code", "GitHub Copilot"), ("cursor-vscode", "Cursor"), ("goose", "Goose"),
    ("some-inspector", ""), ("", ""),
])
def test_the_assistant_the_grid_names(name, assistant):
    assert hosts.assistant_name(_Ctx(name)) == assistant


BODY = {
    "success": True,
    "data": [
        {"photo_id": "019e-1", "alt_description": "Red fox walking in snow",
         "attribution": {"plain": "Photo by Ana on Unsplash (https://pexafy.com/legal/licenses/#unsplash)"},
         "license_type": "free", "urls": {"regular": "https://images.example/1.jpg"},
         "preview_url": "https://thumb.example/1/480w.jpg?e=1&s=a",
         "preview_url_large": "https://thumb.example/1/1280w.jpg?s=b"},
        {"photo_id": "019e-2", "attribution": {"plain": "Photo on Pixabay (https://pexafy.com/legal/licenses/#pixabay)"},
         "urls": {"regular": "https://images.example/2.jpg"},
         "preview_url": "https://thumb.example/2/480w.jpg?e=1&s=c",
         "preview_url_large": "https://thumb.example/2/1280w.jpg?s=d"},
    ],
    "budget": {"message": "2 searches left today without an account."},
}


def test_the_summary_names_the_photographs_and_carries_no_link():
    from pexafy_mcp import previews

    text = previews.grid_summary(json.loads(json.dumps(BODY)), "a red fox in the snow")
    assert "http" not in text
    lines = text.split("\n")
    assert lines[0].startswith("2 photographs for “a red fox in the snow” are on the person's screen")
    assert tooling.PUBLIC_TOOL_NAMES["get_photo_file"] in lines[0]
    assert "they need not be shown again" in lines[0]
    assert lines[1] == "1. 019e-1 — Red fox walking in snow — Photo by Ana on Unsplash — licence: free"
    assert lines[2] == "2. 019e-2 — Photo on Pixabay"
    assert lines[3] == "2 searches left today without an account."
    one = previews.grid_summary({"data": [BODY["data"][0]]})
    assert one.startswith("1 photograph is on the person's screen")


async def _search_as(name: str, monkeypatch, thumb_base: str = ""):
    import importlib

    from fastmcp import Client, FastMCP
    from fastmcp.tools.tool import ToolResult
    from mcp.types import Implementation

    from pexafy_mcp import previews

    monkeypatch.setenv("PEXAFY_PREVIEWS_CHANNEL", "both")
    previews = importlib.reload(previews)
    if thumb_base:
        monkeypatch.setattr(previews, "THUMB_BASE_URL", thumb_base)
    server = FastMCP("grid-contract-test")
    server.add_middleware(previews.SplitPreviews())

    @server.tool(name=tooling.PUBLIC_TOOL_NAMES["search_photos"])
    def search_photos(english_search_sentence: str = ""):
        return ToolResult(structured_content=json.loads(json.dumps(BODY)))

    async with Client(server, client_info=Implementation(name=name, version="1")) as client:
        result = await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"],
                                        {tooling.PUBLIC_QUERY_PARAM: "a red fox"})
    importlib.reload(previews)
    return result


@pytest.mark.asyncio
async def test_a_host_with_the_grid_reads_no_link_and_the_grid_keeps_them(monkeypatch):
    result = await _search_as("cursor-vscode", monkeypatch, thumb_base="https://thumb.example")
    text = result.content[0].text
    assert "http" not in text and text.startswith("2 photographs for “a red fox”")
    # The grid's half keeps what the grid draws — previews, credits — and loses the link
    # it never reads, which VS Code hands the model too.
    photo = result.structured_content["data"][0]
    assert "urls" not in photo
    assert photo["attribution"]["plain"].startswith("Photo by Ana")
    assert result.meta["pexafy/host"] == {"assistant": "Cursor"}
    assert result.meta["pexafy/previews"]["019e-1"]["small"] == "https://thumb.example/1/480w.jpg?e=1&s=a"
    # Its previews come without their address (2026-10-02: 32 image links were still
    # there, two per photograph): a path the grid puts the base back in front of.
    assert photo["preview_url"] == "1/480w.jpg?e=1&s=a"
    assert photo["preview_url_large"] == "1/1280w.jpg?s=b"
    half = json.dumps(result.structured_content)
    assert "thumb.example" not in half and "images.example" not in half


@pytest.mark.asyncio
async def test_a_preview_from_another_address_does_not_stay_in_the_model_s_half(monkeypatch):
    result = await _search_as("cursor-vscode", monkeypatch, thumb_base="https://elsewhere.example")
    photo = result.structured_content["data"][0]
    assert "preview_url" not in photo and "preview_url_large" not in photo
    # The grid still has them, whole, in `_meta`.
    assert result.meta["pexafy/previews"]["019e-1"]["large"] == "https://thumb.example/1/1280w.jpg?s=b"


@pytest.mark.asyncio
async def test_a_client_without_the_grid_reads_the_whole_answer(monkeypatch):
    result = await _search_as("claude-code", monkeypatch)
    body = json.loads(result.content[0].text)
    assert body["data"][0]["urls"]["regular"] == "https://images.example/1.jpg"
    assert result.meta["pexafy/host"] == {"assistant": "Claude"}


def test_the_selection_reads_without_its_image_links_where_the_grid_is_shown(monkeypatch):
    """The grid posts each liked photo's 1280w preview as `image_url`: where the person
    sees the grid, the model reads the selection without it, as it reads a search."""
    import asyncio

    from pexafy_mcp import selection

    monkeypatch.setattr(selection, "key_for_caller", lambda: "k:grid-links")
    selection.clear("k:grid-links")
    item = {"photo_id": "p1", "rank": 1, "url": "https://pexafy.com/photos/p1",
            "image_url": "https://thumb.example/p1/1280w.jpg?s=x"}
    selection.put("k:grid-links", [item], 1, frame="f1")
    monkeypatch.setattr(hosts, "renders_grid", lambda context=None: True)
    shown = asyncio.run(selection.get_selected_photos())["selected_photos"]
    assert [p["photo_id"] for p in shown] == ["p1"]
    assert "image_url" not in shown[0] and shown[0]["url"] == "https://pexafy.com/photos/p1"
    # Elsewhere — Claude Code, a terminal — the link is how the person gets the photo.
    monkeypatch.setattr(hosts, "renders_grid", lambda context=None: False)
    read = asyncio.run(selection.get_selected_photos())["selected_photos"]
    assert read[0]["image_url"] == "https://thumb.example/p1/1280w.jpg?s=x"
    selection.clear("k:grid-links")


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_grid_puts_the_base_back_in_front_of_a_preview_without_address():
    from pexafy_mcp import previews, widget

    js = widget._WIDGET_JS
    start = js.index('const THUMB_BASE = "__THUMB_BASE__";')
    code = js[start:js.index("\n}\n", start) + 2].replace("__THUMB_BASE__", "https://thumb.example")
    code += ("process.stdout.write(JSON.stringify([thumbUrl('1/480w.jpg?e=1&s=a'), "
             "thumbUrl('https://thumb.example/2/1280w.jpg?s=b'), thumbUrl(''), thumbUrl(undefined)]));")
    out = subprocess.run([NODE], input=code, capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["https://thumb.example/1/480w.jpg?e=1&s=a",
                               "https://thumb.example/2/1280w.jpg?s=b", "", ""]
    # The grid served is given the base its server signs under.
    assert f'const THUMB_BASE = "{previews.THUMB_BASE_URL}";' in widget.GRID_HTML
