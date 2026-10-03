"""An uploaded image with words leaves nothing the grid can replay.

The upload itself never travels back to the frame, so an origin made of the words
alone re-asked the by-image tool with no reference: the shape filter failed with
"Provide the reference…", printed into the conversation. No origin means no shape
button, which is what the widget does when `pexafy/origin` is absent. Offline.
"""
from __future__ import annotations

import base64

import httpx
import pytest
from fastmcp import Client

from pexafy_mcp import origin, server, tooling

IMAGE = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
WORDS = {tooling.PUBLIC_QUERY_PARAM: "at night"}
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff" + b"x" * 16).decode()


@pytest.mark.parametrize("upload", [
    {"image_base64": JPEG_B64},
    {"image_file": {"download_url": "https://files.example/x.jpg", "file_id": "f1"}},
])
def test_an_upload_with_words_has_no_origin(upload):
    assert origin.origin_for(IMAGE, {**upload, **WORDS}) is None
    assert origin.origin_for(IMAGE, {**upload, **WORDS,
                                     tooling.PUBLIC_ORIENTATION_PARAM: ["portrait"]}) is None


def test_the_words_alone_are_not_a_reference():
    assert origin.origin_for(IMAGE, dict(WORDS)) is None


@pytest.mark.parametrize("reference", [
    {"photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c0"},
    {"image_url": "https://example.com/a.jpg"},
])
def test_a_replayable_reference_still_travels_with_its_words(reference):
    got = origin.origin_for(IMAGE, {**reference, **WORDS})
    assert got["arguments"] == {**reference, **WORDS}


def test_the_text_search_is_unchanged():
    got = origin.origin_for(tooling.PUBLIC_TOOL_NAMES["search_photos"], dict(WORDS))
    assert got["arguments"] == WORDS


async def test_the_answer_to_an_upload_with_words_carries_no_origin(monkeypatch):
    monkeypatch.setattr(server, "_MAX_IMG_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(200, json={"success": True, "data": []})))
    async with Client(server.build_server()) as client:
        result = await client.call_tool_mcp(IMAGE, {"image_base64": JPEG_B64, **WORDS})
    assert not result.isError
    assert origin.ORIGIN_META_KEY not in (result.meta or {})
