"""Texts name only the tools this server registers, and the grid only where there is one.

The file tool exists only with previews configured, the selection tool only with
PEXAFY_SELECTION_TOOL on, and connect_account only with account linking on. A text that
sends the model to a tool that is not there reads perfectly well and fails. So does one
about a grid nobody sees: previews off, no grid is drawn.
"""
from __future__ import annotations

import re

from fastmcp import Client

from pexafy_mcp import limits, linking, previews, selection, server, tooling

FILE_TOOL = tooling.PUBLIC_TOOL_NAMES["get_photo_file"]
SELECTION_TOOL = tooling.PUBLIC_TOOL_NAMES["get_selected_photos"]
BY_IMAGE = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]


async def _served_texts(mcp) -> tuple[str, dict[str, str], str]:
    tools = {t.name: t.description or "" for t in await mcp.list_tools()}
    resources = await mcp.list_resources()
    widget = " ".join(str((r.meta or {}).get("openai/widgetDescription", "")) for r in resources)
    return mcp.instructions or "", tools, widget


async def test_without_previews_nothing_names_the_file_tool(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", False)
    instructions, tools, _ = await _served_texts(server.build_server())
    assert FILE_TOOL not in tools
    assert FILE_TOOL not in instructions
    for name, description in tools.items():
        assert FILE_TOOL not in description, name


async def test_with_previews_the_file_tool_is_named_where_it_helps(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    instructions, tools, _ = await _served_texts(server.build_server())
    assert FILE_TOOL in tools
    assert f"`{FILE_TOOL}`" in instructions
    assert FILE_TOOL in tools[tooling.PUBLIC_TOOL_NAMES["search_photos"]]
    assert FILE_TOOL in tools[SELECTION_TOOL]


async def test_without_a_grid_there_is_no_selection_to_read(monkeypatch):
    """No thumbnail CDN, no grid — the default for a server run on its own. Nothing can
    be liked, so the tool that reads what was liked is not served, and no text names it,
    whatever PEXAFY_SELECTION_TOOL says."""
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", False)
    monkeypatch.setattr(selection, "TOOL_ENABLED", True)
    mcp = server.build_server()
    instructions, tools, widget = await _served_texts(mcp)
    assert SELECTION_TOOL not in tools
    assert SELECTION_TOOL not in instructions
    for name, description in tools.items():
        assert SELECTION_TOOL not in description, name
    assert widget == ""                                   # no grid resource either
    assert instructions == server.server_instructions(file_tool=False, selection_tool=False,
                                                      grid=False)


async def test_without_the_selection_tool_nothing_names_it(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(selection, "TOOL_ENABLED", False)
    instructions, tools, widget = await _served_texts(server.build_server())
    assert SELECTION_TOOL not in tools
    assert SELECTION_TOOL not in instructions
    assert SELECTION_TOOL not in widget and "passes them to you" in widget


async def test_the_full_configuration_serves_the_published_texts(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    instructions, tools, _ = await _served_texts(server.build_server())
    assert instructions == server.SERVER_INSTRUCTIONS
    assert tools[SELECTION_TOOL] == selection.SELECTED_DESCRIPTION
    assert tools[BY_IMAGE] == server.image_tool_description()
    assert tools[linking.CONNECT_TOOL] == linking.CONNECT_DESCRIPTION


# ── Without a grid, no text names it ─────────────────────────────────────────
# PEXAFY_MCP_PREVIEWS=0 (the way back) or a server run on its own: no grid is drawn,
# and a sentence about one — a photo "liked in the Pexafy grid", results that "also
# appear to the person as a grid", the grid's account button — reads perfectly well
# and is false.

def _strings(node, path: str = ""):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")


async def _every_served_string(mcp) -> list[tuple[str, str]]:
    """Every string `initialize`, `tools/list`, `prompts/list` and `resources/list` send
    — descriptions, titles, every parameter and output field, `_meta` — as (where, text)."""
    found = []
    async with Client(mcp) as client:
        found.append(("instructions", client.initialize_result.instructions or ""))
        for kind, items in (("tool", await client.list_tools()),
                            ("prompt", await client.list_prompts()),
                            ("resource", await client.list_resources())):
            for item in items:
                dumped = item.model_dump(mode="json", by_alias=True, exclude_none=True)
                found += [(f"{kind} {getattr(item, 'name', '')}: {where}", text)
                          for where, text in _strings(dumped)]
    return found


def _naming_the_grid(served) -> list[str]:
    return [f"{where}: {text[:160]}" for where, text in served
            if re.search(r"(?i)\bgrid\b", text) or SELECTION_TOOL in text]


async def test_without_a_grid_no_served_text_names_it(monkeypatch):
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", False)
    monkeypatch.setattr(selection, "TOOL_ENABLED", True)
    monkeypatch.setattr(linking, "ENABLED", True)
    served = await _every_served_string(server.build_server())
    assert not _naming_the_grid(served), "\n".join(_naming_the_grid(served))
    # What stays is still said: the catalogue photo as a reference, and the probe.
    text = dict(served)
    assert "or `photo_id` for a Pexafy photo returned in an earlier search." in text["instructions"]
    assert "`check_only` reads the state without opening a prompt" in text[
        f"tool {linking.CONNECT_TOOL}: description"]


async def test_with_the_grid_the_same_check_finds_it_everywhere_it_is(monkeypatch):
    """The check above proves something only if it finds the grid where there is one."""
    monkeypatch.setattr(previews, "PREVIEWS_AVAILABLE", True)
    monkeypatch.setattr(linking, "ENABLED", True)
    served = await _every_served_string(server.build_server())
    places = {where.split(":")[0] for where in _naming_the_grid(served)}
    assert {"instructions", f"tool {tooling.PUBLIC_TOOL_NAMES['search_photos']}",
            f"tool {BY_IMAGE}", f"tool {linking.CONNECT_TOOL}"} <= places, places


async def test_the_daily_wall_names_connect_account_only_when_it_exists(monkeypatch):
    monkeypatch.setattr(linking, "ENABLED", True)
    assert "call connect_account" in await limits.daily_quota_message(
        limit=100, retry_after=3600, host_can_connect=True)
    monkeypatch.setattr(linking, "ENABLED", False)
    text = await limits.daily_quota_message(limit=100, retry_after=3600, host_can_connect=True)
    assert "connect_account" not in text
    assert "A free Pexafy account raises the limit" in text and "Do not retry the search." in text
