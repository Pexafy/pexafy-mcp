"""The server builds offline and exposes exactly the search core."""
from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from pexafy_mcp import server

EXPECTED_TOOLS = {"search_photos", "search_photos_by_image", "photo_similar"}


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
    # A title must exist for each tool we ship (drives the client display name).
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
