"""A rank sent where a photo_id belongs is answered, not forwarded."""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from pexafy_mcp import server, tooling


async def test_a_rank_sent_as_a_photo_id_is_refused_with_the_fix_in_the_message(fake_api):
    """A rank sent as the photo id ("more like #6") must not reach the API, whose answer
    — `500 SEARCH_ERROR — Search service temporarily unavailable` — ChatGPT read as an
    outage: it told the user the service had failed, and offered to search by words
    instead. A lost follow-up, on the one move the result grid is built to invite.

    The message has to carry the correction, not just the refusal — it is what the next
    turn is written from.
    """
    async with Client(server.build_server()) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "6"})
    message = str(exc.value)
    # The correction: results carry a rank, and the mistake is not to count but to send
    # the number where a `photo_id` is expected.
    assert "a `rank` names a photograph" in message
    assert "photo_id" in message
    # A real id in the message: an assistant that has one to copy makes fewer guesses.
    assert "019e1ecb-0039-7da6-b1ca-987ee4d337c0" in message
    # And it must not reach the API — the whole point is that its answer is misleading.
    assert fake_api.requests == []


async def test_a_real_uuid_is_not_touched(fake_api):
    """The guard separates a UUID from a rank; it must not stand between a good call
    and the API."""
    async with Client(server.build_server()) as client:
        await client.call_tool(
            tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": "019e1ecb-0039-7da6-b1ca-987ee4d337c0"}
        )
    [request] = fake_api.requests
    assert request.url.params["photo_id"] == "019e1ecb-0039-7da6-b1ca-987ee4d337c0"


async def test_a_search_with_no_words_says_what_to_send(fake_api):
    """The sentence is required, and the requirement has to be legible three ways.

    The schema covers a caller that reads it. It does not cover a host still on the
    published 0.4.12 snapshot, where `q` was optional and a filter-only search was legal:
    it calls without it and gets `TypeError: Missing required argument(s)`, which is
    accurate and useless. And `required` constrains presence, not value — an empty string
    would reach the API as a raw 400, a blank one would search for whitespace.
    """
    async with Client(server.build_server()) as client:
        for arguments in ({}, {"english_search_sentence": ""}, {"english_search_sentence": "   "}):
            with pytest.raises(ToolError) as exc:
                await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"], arguments)
            message = str(exc.value)
            assert "`english_search_sentence` is required" in message, arguments
            # It names the way out, not just the rule: the filters are gone, send words.
            assert "only thing this tool searches by" in message, arguments
            assert "one English sentence" in message, arguments
            # And it never reaches the API: no raw 400, no search for whitespace.
            assert fake_api.requests == [], arguments


async def test_a_real_query_is_not_touched(fake_api):
    """The guard must not stand between a good search and the API."""
    async with Client(server.build_server()) as client:
        await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"], {"english_search_sentence": "a red bicycle"})
    [request] = fake_api.requests
    assert request.url.params["q"] == "a red bicycle"


def test_the_query_is_bound_in_the_schema_at_both_ends():
    """A `required` that still accepts "" is not a requirement."""
    from pexafy_mcp import tooling
    q = tooling._param_overrides()["q"]
    assert q["required"] is True
    assert q["schema"]["minLength"] == 1
    assert q["schema"]["maxLength"] == 250
