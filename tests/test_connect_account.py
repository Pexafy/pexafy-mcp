"""connect_account asked for by somebody who is already connected.

The tool declares an output schema, so FastMCP refuses a bare string as its result
("structured_content must be a dict or None"): the person got an error instead of
"there is nothing to connect". Offline: this path never reaches the API.
"""
from __future__ import annotations

import jsonschema
from fastmcp import Client
from mcp.types import TextContent

from pexafy_mcp import anonymous, linking, server


async def test_the_answer_matches_the_declared_schema():
    reset = anonymous.current.set(None)  # no anonymous identity: a key or OAuth caller
    try:
        result = await linking.connect_account()
    finally:
        anonymous.current.reset(reset)
    assert result.structured_content == {"connected": True, "budget": None}
    assert [c.text for c in result.content if isinstance(c, TextContent)] == [
        linking.ALREADY_CONNECTED]


async def test_an_already_connected_caller_gets_an_answer_not_an_error(monkeypatch):
    monkeypatch.setattr(linking, "ENABLED", True)
    mcp = server.build_server()
    tool = next(t for t in await mcp.list_tools() if t.name == linking.CONNECT_TOOL)
    async with Client(mcp) as client:
        result = await client.call_tool(linking.CONNECT_TOOL, {}, raise_on_error=False)
    assert not result.is_error, result.content
    assert result.content[0].text == linking.ALREADY_CONNECTED
    assert result.structured_content == {"connected": True, "budget": None}
    jsonschema.validate(result.structured_content, tool.output_schema)
