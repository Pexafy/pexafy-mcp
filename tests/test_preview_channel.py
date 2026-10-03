"""Which half of the answer the grid's image links travel in.

`preview_url` and `preview_url_large` are read by the widget and by nothing else. The
model has no network: it cannot open either one. Measured on a real search, the two
signed URLs were 3,936 characters per answer — 28% of what was left after the curation
— spent on a reader that can only skip them.

MCP's `_meta` is the host's half: the widget gets it, the model does not. These tests
pin the three positions of the switch, and the last one drives a real client against a
real server, because the middleware alone proves nothing about what reaches the wire.

    pytest tests/test_preview_channel.py -v
"""
from __future__ import annotations

import importlib

import pytest

from pexafy_mcp import tooling


def _body():
    """A search answer as _enrich_response leaves it."""
    return {
        "success": True,
        "data": [
            {"photo_id": "019e-1", "width": 4000,
             "preview_url": "https://cdn.example/019e-1/480w.jpg?e=1&s=aa",
             "preview_url_large": "https://cdn.example/019e-1/1280w.jpg?s=bb"},
            {"photo_id": "019e-2", "width": 3000,
             "preview_url": "https://cdn.example/019e-2/480w.jpg?e=1&s=cc",
             "preview_url_large": "https://cdn.example/019e-2/1280w.jpg?s=dd"},
        ],
    }


def _previews(channel, monkeypatch):
    """previews.py as it behaves under PEXAFY_PREVIEWS_CHANNEL=<channel>."""
    from pexafy_mcp import previews

    monkeypatch.setenv("PEXAFY_PREVIEWS_CHANNEL", channel)
    return importlib.reload(previews)


@pytest.fixture(autouse=True)
def _restore():
    """Leave the module as the rest of the suite expects to find it."""
    yield
    from pexafy_mcp import previews

    importlib.reload(previews)


class TestBoth:
    """The deployed default: both halves carry them, so nothing can break."""

    def test_the_links_are_keyed_by_photo_id(self, monkeypatch):
        p = _previews("both", monkeypatch)
        links = p.previews_for_meta(_body())
        assert set(links) == {"019e-1", "019e-2"}
        assert links["019e-1"]["small"].endswith("480w.jpg?e=1&s=aa")
        assert links["019e-1"]["large"].endswith("1280w.jpg?s=bb")

    def test_nothing_is_taken_out_of_the_model_s_half(self, monkeypatch):
        p = _previews("both", monkeypatch)
        body = _body()
        p.strip_previews_from_content(body)
        assert body["data"][0]["preview_url"]
        assert body["data"][0]["preview_url_large"]


class TestMeta:
    """The saving, once a host is proven to carry `_meta` to the frame."""

    def test_the_links_leave_the_model_s_half(self, monkeypatch):
        p = _previews("meta", monkeypatch)
        body = _body()
        assert p.previews_for_meta(body)
        p.strip_previews_from_content(body)
        for photo in body["data"]:
            assert "preview_url" not in photo
            assert "preview_url_large" not in photo
            # Everything the model DOES read stays.
            assert photo["photo_id"] and photo["width"]


class TestContent:
    """The way back: no second half at all."""

    def test_nothing_is_offered_for_meta(self, monkeypatch):
        p = _previews("content", monkeypatch)
        assert p.previews_for_meta(_body()) is None

    def test_and_nothing_is_stripped(self, monkeypatch):
        p = _previews("content", monkeypatch)
        body = _body()
        p.strip_previews_from_content(body)
        assert body["data"][0]["preview_url"]


class TestShapesItMustNotChokeOn:
    def test_an_empty_answer_offers_nothing(self, monkeypatch):
        p = _previews("both", monkeypatch)
        assert p.previews_for_meta({"success": True, "data": []}) is None
        assert p.previews_for_meta(None) is None
        assert p.previews_for_meta("not a body") is None

    def test_a_photo_without_previews_is_skipped_not_crashed(self, monkeypatch):
        p = _previews("both", monkeypatch)
        body = {"data": [{"photo_id": "019e-3"}, None, {"width": 10}]}
        assert p.previews_for_meta(body) is None

    def test_the_wall_has_no_photos_and_survives(self, monkeypatch):
        p = _previews("meta", monkeypatch)
        wall = {"success": True, "data": [], "budget": {"state": "exhausted"}}
        assert p.previews_for_meta(wall) is None
        p.strip_previews_from_content(wall)  # must not raise


@pytest.mark.asyncio
async def test_the_links_reach_the_wire_on_meta_not_in_the_model_s_half(monkeypatch):
    """The one that matters: a real client, a real server, the whole pipeline.

    The middleware is the last hop before the answer leaves, so it has to see the
    result whoever built it. Called directly it would pass while the wire carried
    something else — the mistake that cost a preprod round on the account challenge.
    """
    from fastmcp import Client, FastMCP
    from fastmcp.tools.tool import ToolResult

    p = _previews("meta", monkeypatch)

    server = FastMCP("previews-test")
    server.add_middleware(p.SplitPreviews())

    @server.tool(name=tooling.PUBLIC_TOOL_NAMES["search_photos"])
    def search_photos():
        return ToolResult(structured_content=_body())

    async with Client(server) as client:
        result = await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"], {})

    links = (getattr(result, "meta", None) or {})[p.PREVIEW_META_KEY]
    assert links["019e-2"]["small"].endswith("480w.jpg?e=1&s=cc")
    assert links["019e-2"]["large"].endswith("1280w.jpg?s=dd")
    for photo in result.structured_content["data"]:
        assert "preview_url" not in photo


@pytest.mark.asyncio
async def test_a_meta_the_result_already_carries_is_kept(monkeypatch):
    """A result may arrive with a `_meta` of its own — the account-link challenge is
    one. Merged, never replaced."""
    from fastmcp import Client, FastMCP
    from fastmcp.tools.tool import ToolResult

    p = _previews("both", monkeypatch)

    server = FastMCP("previews-test")
    server.add_middleware(p.SplitPreviews())

    @server.tool(name=tooling.PUBLIC_TOOL_NAMES["search_photos"])
    def search_photos():
        return ToolResult(structured_content=_body(), meta={"mine": "kept"})

    async with Client(server) as client:
        result = await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"], {})

    meta = getattr(result, "meta", None) or {}
    assert meta["mine"] == "kept"
    assert p.PREVIEW_META_KEY in meta


@pytest.mark.asyncio
async def test_a_tool_that_returns_no_photographs_is_left_alone(monkeypatch):
    from fastmcp import Client, FastMCP

    p = _previews("meta", monkeypatch)

    server = FastMCP("previews-test")
    server.add_middleware(p.SplitPreviews())

    @server.tool
    def connect_account():
        return "Already connected."

    async with Client(server) as client:
        result = await client.call_tool("connect_account", {})

    assert p.PREVIEW_META_KEY not in (getattr(result, "meta", None) or {})
