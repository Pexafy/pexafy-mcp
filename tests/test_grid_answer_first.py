"""Where the grid is shown, Claude aside, each search's description opens on the grid.

Asked why, after the Pexafy grid, it had added pictures of its own that could not load
and links to stock sites, ChatGPT answered that it took Pexafy for a source of results
rather than for the answer on screen, and that the words saying so came last. With
tooling.grid_answer_paragraph first in the description, five searches in ChatGPT added
no picture below the grid, against 12 to 21 when the same words came at the end or in the
result (2026-10-15). It is listed only to a host that shows the grid and is not Claude,
whose directory asks a description not to tell Claude how to behave; a host without the
grid — Claude Code, Codex, a terminal, an unknown client — reads the list as before.
ChatGPT's answer also says, in the data, how it is shown (`presentation`).
"""
from __future__ import annotations

import json

import pytest
from fastmcp import Client
from mcp.types import Implementation

from pexafy_mcp import previews, server, tooling

SEARCHES = (tooling.PUBLIC_TOOL_NAMES["search_photos"],
            tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"])


async def _listed(name: str, monkeypatch, grid: bool = True) -> dict:
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", grid)
    async with Client(server.build_server(),
                      client_info=Implementation(name=name, version="1")) as client:
        return {tool.name: tool for tool in await client.list_tools()}


@pytest.mark.parametrize("name", ["openai-mcp", "Visual Studio Code", "cursor-vscode", "goose"])
async def test_a_host_with_the_grid_reads_the_grid_first(name, monkeypatch):
    tools = await _listed(name, monkeypatch)
    first = tooling.grid_answer_paragraph()
    for search in SEARCHES:
        assert tools[search].description.startswith(first + "\n\n"), (name, search)
        # …and the rest of it is unchanged: the triggers still follow.
        assert "Use this when" in tools[search].description[len(first):]
    # The other tools are listed as they are.
    assert not tools[tooling.PUBLIC_TOOL_NAMES["get_photo_file"]].description.startswith(first)


@pytest.mark.parametrize("name", ["claude-ai", "Anthropic/ClaudeAI", "claude-code",
                                  "codex-mcp-client", "mcp", "some-inspector", ""])
async def test_claude_and_hosts_without_the_grid_read_the_list_as_before(name, monkeypatch):
    tools = await _listed(name, monkeypatch)
    for search in SEARCHES:
        assert "complete visual answer" not in tools[search].description, (name, search)


async def test_without_previews_no_host_reads_it(monkeypatch):
    tools = await _listed("openai-mcp", monkeypatch, grid=False)
    assert "complete visual answer" not in tools[SEARCHES[0]].description


def test_the_paragraph_says_what_was_measured_and_names_no_other_tool():
    text = tooling.grid_answer_paragraph()
    assert text.startswith("Pexafy Stock Photos is a full photo search")
    assert "complete visual answer to the request" in text
    assert "The reply adds no second gallery: no inline images and no photo links." in text
    assert tooling.PUBLIC_TOOL_NAMES["get_selected_photos"] in text
    # No word about another tool or service, nothing promotional.
    for refused in ("instead", "rather than", "other tools", "image search", "generate",
                    "best", "advanced", "official"):
        assert refused not in text.lower(), refused


async def _search_as(name: str, monkeypatch):
    import importlib

    from fastmcp import FastMCP
    from fastmcp.tools.tool import ToolResult

    monkeypatch.setenv("PEXAFY_PREVIEWS_CHANNEL", "both")
    mod = importlib.reload(previews)
    monkeypatch.setattr(mod, "THUMB_BASE_URL", "https://thumb.example")
    mcp = FastMCP("presentation-test")
    mcp.add_middleware(mod.SplitPreviews())
    body = {"success": True, "data": [{
        "photo_id": "019e-1", "alt_description": "Red fox walking in snow",
        "attribution": {"plain": "Photo by Ana on Unsplash (https://pexafy.com/legal/licenses/#unsplash)"},
        "source_image_url": "https://unsplash.com/photos/1",
        "preview_url": "https://thumb.example/1/480w.jpg?e=1&s=a",
        "preview_url_large": "https://thumb.example/1/1280w.jpg?s=b"}]}

    @mcp.tool(name=tooling.PUBLIC_TOOL_NAMES["search_photos"])
    def search_photos(english_search_sentence: str = ""):
        return ToolResult(structured_content=json.loads(json.dumps(body)))

    async with Client(mcp, client_info=Implementation(name=name, version="1")) as client:
        result = await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"],
                                        {tooling.PUBLIC_QUERY_PARAM: "a red fox"})
    importlib.reload(previews)
    return result


async def test_chatgpt_s_answer_says_how_it_is_shown(monkeypatch):
    result = await _search_as("openai-mcp", monkeypatch)
    assert result.structured_content[previews.PRESENTATION_KEY] == {
        "mode": "grid", "grid_already_rendered": True, "show_inline_images": False,
        "show_photo_links": False,
        "next_step": ("The person likes the photographs they want in the grid; "
                      f"{tooling.PUBLIC_TOOL_NAMES['get_selected_photos']} returns them.")}


@pytest.mark.parametrize("name", ["cursor-vscode", "claude-code", "mcp"])
async def test_other_hosts_answers_carry_no_presentation(name, monkeypatch):
    result = await _search_as(name, monkeypatch)
    assert previews.PRESENTATION_KEY not in result.structured_content


async def test_the_presentation_is_declared_in_the_search_schema(monkeypatch):
    tools = await _listed("mcp", monkeypatch)
    declared = tools[SEARCHES[0]].outputSchema["properties"]
    assert "presentation" in declared
    assert set(declared["presentation"]["properties"]) == {
        "mode", "grid_already_rendered", "show_inline_images", "show_photo_links", "next_step"}
