"""A single shape sent as a bare string is read as a one-item list, on both searches.

It used to skip the enum guard: on the text search "vertical" went to the API, which
drops an unknown shape in silence (an unfiltered grid, reported as filtered); on the
by-image tool even a valid "portrait" failed with a raw pydantic error. Offline: the API
is an httpx.MockTransport.
"""
from __future__ import annotations

import httpx
import pytest
from fastmcp import Client

from pexafy_mcp import server, tooling

TEXT = tooling.PUBLIC_TOOL_NAMES["search_photos"]
IMAGE = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
SHAPE = tooling.PUBLIC_ORIENTATION_PARAM
PHOTO = "019e1ecb-0039-7da6-b1ca-987ee4d337c0"
CALLS = {
    TEXT: {tooling.PUBLIC_QUERY_PARAM: "a tree"},
    IMAGE: {"photo_id": PHOTO},
}


@pytest.fixture
def api(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": []})

    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(handler))
    return seen


async def _call(tool, shape):
    async with Client(server.build_server()) as client:
        return await client.call_tool(tool, {**CALLS[tool], SHAPE: shape}, raise_on_error=False)


@pytest.mark.parametrize("tool", [TEXT, IMAGE])
async def test_an_unknown_shape_as_a_string_is_refused_before_the_api(api, tool):
    result = await _call(tool, "vertical")
    assert result.is_error
    text = result.content[0].text
    assert text.startswith("'vertical' is not a photo shape.")
    assert "validation error" not in text
    assert api == []


@pytest.mark.parametrize("tool", [TEXT, IMAGE])
async def test_a_known_shape_as_a_string_filters(api, tool):
    result = await _call(tool, "portrait")
    assert not result.is_error, result.content
    [request] = api
    assert request.url.params.get_list(tooling.API_ORIENTATION_PARAM) == ["portrait"]
