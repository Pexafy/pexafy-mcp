"""Whatever is sent as a `photo_id`, a non-UUID is answered by the guard.

A rank sent as a JSON number (6, not "6") slipped past it and met pydantic, whose error
names the tool's old internal name. And the guard's message spoke of a position for
anything: a URL sent as `photo_id` was told "a short number like this is a position".
Offline: the API is an httpx.MockTransport, and must never be reached here.
"""
from __future__ import annotations

import httpx
import pytest
from fastmcp import Client

from pexafy_mcp import guards, server, tooling

IMAGE = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
FILE = tooling.PUBLIC_TOOL_NAMES["get_photo_file"]
UUID = "019e1ecb-0039-7da6-b1ca-987ee4d337c0"


@pytest.fixture
def api(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": []})

    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(handler))
    return seen


async def _by_image(photo_id):
    async with Client(server.build_server()) as client:
        return await client.call_tool(IMAGE, {"photo_id": photo_id}, raise_on_error=False)


@pytest.mark.parametrize("rank", [6, 12, "6", "#6"])
async def test_a_rank_in_any_form_is_answered_as_a_rank(api, rank):
    result = await _by_image(rank)
    assert result.is_error
    text = result.content[0].text
    assert "is not a photo_id" in text
    assert "a `rank` names a photograph" in text
    assert UUID in text
    assert "validation error" not in text and "search_photos_by_image" not in text
    assert api == []


@pytest.mark.parametrize("value", [
    "https://example.com/photo.jpg", "a red bicycle", 3.5, True, 123456789,
])
async def test_anything_else_is_not_told_it_is_a_position(api, value):
    result = await _by_image(value)
    assert result.is_error
    text = result.content[0].text
    assert "is not a photo_id" in text
    assert UUID in text
    assert "position" not in text and "rank" not in text
    assert "`image_url`" in text  # the by-image tool has somewhere else for a picture
    assert "validation error" not in text
    assert api == []


async def test_a_long_value_is_not_echoed_whole(api):
    result = await _by_image("data:image/jpeg;base64," + "A" * 5000)
    assert len(result.content[0].text) < 600


async def test_a_real_id_goes_through(api):
    result = await _by_image(UUID)
    assert not result.is_error, result.content
    assert api[0].url.params["photo_id"] == UUID


async def test_the_file_tool_is_guarded_the_same_way():
    class _Message:
        name = FILE
        arguments = {"photo_id": 6}

    class _Context:
        message = _Message()

    async def call_next(_context):
        raise AssertionError("a rank must not reach the tool")

    with pytest.raises(Exception) as caught:
        await guards.GuardPhotoId().on_call_tool(_Context(), call_next)
    assert "a `rank` names a photograph" in str(caught.value)
    assert "`image_url`" not in str(caught.value)  # the file tool takes no picture
