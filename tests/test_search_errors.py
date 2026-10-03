"""A failed TEXT search reads like a failed image search: a sentence, not a dump.

The text search is the generated OpenAPI tool, and a non-2xx answer reached the model as
FastMCP's raw "HTTP error 503: Service Unavailable - {'success': False, …}". The 429
path (plan limits, the wall as an empty grid) is left exactly as it was. Offline: the
API is an httpx.MockTransport.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client

from pexafy_mcp import server, tooling

TEXT = tooling.PUBLIC_TOOL_NAMES["search_photos"]
IMAGE = tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"]
OUTAGE = {"success": False, "error": {"code": "SEARCH_ERROR",
                                      "message": "Search service temporarily unavailable"}}


def _api(monkeypatch, handler):
    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(handler))


async def _search(tool=TEXT, **arguments):
    arguments = arguments or {tooling.PUBLIC_QUERY_PARAM: "a red bicycle"}
    async with Client(server.build_server()) as client:
        return await client.call_tool(tool, arguments, raise_on_error=False)


@pytest.mark.parametrize("status", [500, 503])
async def test_an_outage_is_a_sentence_with_the_apis_own_words(monkeypatch, status):
    _api(monkeypatch, lambda r: httpx.Response(status, json=OUTAGE))
    result = await _search()
    assert result.is_error
    text = result.content[0].text
    assert text == "Pexafy could not run that search. Search service temporarily unavailable"


async def test_a_refusal_without_a_readable_body_gets_the_plain_sentence(monkeypatch):
    _api(monkeypatch, lambda r: httpx.Response(
        502, headers={"content-type": "text/html"}, text="<html>Bad gateway</html>"))
    result = await _search()
    assert result.is_error
    assert result.content[0].text == "Pexafy could not run that search."


async def test_a_bad_key_says_what_the_api_says(monkeypatch):
    _api(monkeypatch, lambda r: httpx.Response(401, json={
        "success": False, "error": {"code": "INVALID_API_KEY", "message": "Invalid API key"}}))
    result = await _search()
    assert result.content[0].text == "Pexafy could not run that search. Invalid API key"


async def test_the_image_search_keeps_its_own_wording(monkeypatch):
    _api(monkeypatch, lambda r: httpx.Response(503, json=OUTAGE))
    result = await _search(IMAGE, photo_id="019e1ecb-0039-7da6-b1ca-987ee4d337c0")
    assert result.content[0].text == (
        "Pexafy could not find photos like that one. Search service temporarily unavailable")


def _spent_day(code):
    body = json.dumps({"success": False, "data": None,
                       "error": {"code": code, "message": "nope"}})
    headers = {"X-Plan": "anonymous", "X-Daily-Quota-Limit": "100",
               "X-Daily-Quota-Remaining": "0", "Retry-After": "25200",
               "content-type": "application/json"}
    if code == "RATE_LIMITED":
        headers["Retry-After"] = "12"
    return lambda r: httpx.Response(429, headers=headers, content=body.encode())


async def test_the_wall_is_still_an_empty_grid(monkeypatch):
    monkeypatch.setattr(server, "WALL_AS_GRID", True)
    _api(monkeypatch, _spent_day("DAILY_QUOTA_EXCEEDED"))
    result = await _search()
    assert not result.is_error
    assert result.structured_content["data"] == []
    assert "used up" in result.structured_content["notice"]


@pytest.mark.parametrize(("client_name", "named"), [
    ("openai-mcp", True),     # ChatGPT: shows its own account-connection prompt
    ("claude-code", False),   # Claude Code without an account: nothing would open
])
async def test_the_wall_names_connect_account_only_to_a_host_that_prompts(
        monkeypatch, client_name, named):
    """The whole path: a real client names itself at `initialize`, and the refusal is
    worded inside the tool call, where that session is in scope."""
    from mcp.types import Implementation

    from pexafy_mcp import linking

    monkeypatch.setattr(server, "WALL_AS_GRID", True)
    monkeypatch.setattr(linking, "ENABLED", True)
    _api(monkeypatch, _spent_day("DAILY_QUOTA_EXCEEDED"))
    async with Client(server.build_server(),
                      client_info=Implementation(name=client_name, version="1")) as client:
        result = await client.call_tool(TEXT, {tooling.PUBLIC_QUERY_PARAM: "a red bicycle"},
                                        raise_on_error=False)
    notice = result.structured_content["notice"]
    assert "used up" in notice and notice.endswith("Do not retry the search.")
    assert ("call connect_account" in notice) is named


async def test_a_rate_limit_keeps_its_own_message(monkeypatch):
    _api(monkeypatch, _spent_day("RATE_LIMITED"))
    result = await _search()
    assert result.is_error
    assert "try once more" in result.content[0].text
    assert "could not run that search" not in result.content[0].text


async def test_other_calls_on_the_same_client_are_not_touched(monkeypatch):
    """The probe's usage read and the photo metadata read handle their own failures."""
    _api(monkeypatch, lambda r: httpx.Response(500, json=OUTAGE))
    response = await server.client.get("/api/v1/usage")
    assert response.status_code == 500
