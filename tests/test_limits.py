"""What the assistant is told when a plan limit stops a search.

Every assertion here is really one rule, read from OpenAI's plugin policy: "Plugins
must not display subscription plans, initiate new subscriptions, or promote
upgrades", while "the plugin may explain that [a feature requires a different plan]".
So each message must carry the fact and nothing that sells: no tier name, no price,
no link to pricing. And each is a report from Pexafy, in the third person: the server
never speaks as the assistant ("I can't…", "ask me again") — Anthropic's directory
reads that as the server putting words in the assistant's mouth.
"""
from __future__ import annotations

import pytest
from unittest import mock

from pexafy_mcp import limits

# Anything that would read as selling inside someone's conversation.
SALES = ("upgrade", "Upgrade", "pricing", "/mo", "€", "$", "Starter", "Pro plan")


def _assert_no_sales_pitch(message: str) -> None:
    for token in SALES:
        assert token not in message, f"{token!r} in: {message}"


def test_number_formatting():
    assert limits._n(0) == "unlimited"
    assert limits._n(1500) == "1,500"
    assert limits._n(None) == "None"


def test_key_pluralisation():
    assert limits._keys(1) == "1 API key"
    assert limits._keys(3) == "3 API keys"


@pytest.mark.asyncio
async def test_monthly_quota_states_the_limit_and_the_plan():
    """Enough for someone to understand why it stopped, and act on their own."""
    message = await limits.monthly_quota_message("free", 5000)
    assert "5,000" in message
    assert "Free plan" in message
    _assert_no_sales_pitch(message)


@pytest.mark.asyncio
async def test_monthly_quota_reads_without_a_number():
    message = await limits.monthly_quota_message("free")
    assert "this month's searches" in message
    _assert_no_sales_pitch(message)


@pytest.mark.asyncio
async def test_rate_limit_keeps_the_wait_instruction():
    """Without it an assistant retries in a loop, burning the same budget."""
    message = await limits.rate_limit_message("free", 20, 30)
    assert "Wait 30s" in message
    assert "20 searches per minute" in message
    _assert_no_sales_pitch(message)


@pytest.mark.asyncio
async def test_rate_limit_without_a_retry_after_still_says_not_to_loop():
    message = await limits.rate_limit_message("free", 20, None)
    assert "don't retry in a loop" in message
    _assert_no_sales_pitch(message)


@pytest.mark.asyncio
async def test_a_caller_without_an_account_is_not_given_a_plan():
    """"The Anonymous plan" is a name the API uses, not one the person has: every other
    text says "without an account", and the model repeats what it reads."""
    message = await limits.rate_limit_message("anonymous", 30, 30)
    assert message == (
        "Pexafy refused this search: searches are arriving faster than the allowance "
        "without an account allows (30 searches per minute). Wait 30s, then try once more.")
    assert "plan" not in message.lower()
    # A caller with an account keeps the name of their plan.
    assert "the Free plan allows" in await limits.rate_limit_message("free", 20, 30)


def test_key_limit_points_at_the_user_s_own_keys():
    """Naming the key page is not an upsell — it is the way out of the block."""
    message = limits.key_limit_message({"plan_label": "Free", "max": 1})
    assert "1 API key" in message
    assert limits.CONNECTORS_URL in message
    _assert_no_sales_pitch(message)


def test_key_limit_speaks_the_language_of_the_page_it_links_to():
    """It sends the user to a page listing API keys; it must say API keys."""
    message = limits.key_limit_message({"plan_label": "Starter", "max": 3})
    assert "allows 3 API keys, all in use" in message
    assert "connector" not in message.lower()
    one = limits.key_limit_message({"plan_label": "Free", "max": 1})
    assert "allows 1 API key, already in use" in one


def test_key_limit_never_asks_for_a_reconnection():
    """The token is resolved on every request: freeing a slot is enough."""
    message = limits.key_limit_message({"plan_label": "Starter", "max": 3})
    assert "nothing to reconnect" in message
    assert "then reconnect" not in message


def test_key_limit_without_a_known_maximum():
    message = limits.key_limit_message({})
    assert "has reached its API key limit" in message
    _assert_no_sales_pitch(message)


@pytest.mark.parametrize("ctx", [{}, {"plan_label": ""}, {"max": 2}, {"plan_label": None, "max": 1}])
def test_key_limit_without_a_plan_name_names_the_account_once(ctx):
    message = limits.key_limit_message(ctx)
    assert "this account's plan" in message
    assert message.count("this account's") == 1
    # A report about the account, not the assistant speaking to the person.
    assert "your" not in message.lower()


@pytest.mark.asyncio
async def test_every_limit_message_is_a_report_from_pexafy_not_the_assistant_speaking():
    """"I can't search Pexafy… ask me again" was the server talking AS the assistant;
    each message now opens on what Pexafy did, and none speaks in the first person."""
    messages = [
        limits.key_limit_message({"plan_label": "Free", "max": 1}),
        limits.key_limit_message({}),
        await limits.monthly_quota_message("free", 5000),
        await limits.daily_quota_message("anonymous", 100, 3600),
        await limits.daily_quota_message("anonymous", 100, 3600, host_can_connect=True),
        await limits.rate_limit_message("free", 20, 30),
        await limits.rate_limit_message("anonymous", 30, 30),
    ]
    for message in messages:
        assert message.startswith("Pexafy refused this "), message
        for first_person in ("I can't", "I cannot", "ask me", " me ", " my "):
            assert first_person not in message, (first_person, message)


# ── Daily quota: the third case, and not the other two ───────────────────────

@pytest.mark.asyncio
async def test_daily_quota_message_says_the_budget_comes_back():
    msg = await limits.daily_quota_message("anonymous", 100, 7200)
    assert "100" in msg
    assert "2 hours" in msg
    assert "month" not in msg.lower(), "a daily budget is not a monthly one"


@pytest.mark.asyncio
async def test_daily_quota_message_falls_back_to_midnight():
    msg = await limits.daily_quota_message("anonymous", 100, None)
    assert "midnight UTC" in msg


@pytest.mark.asyncio
async def test_daily_quota_message_states_the_account_once_without_a_pitch():
    """OpenAI's plugin policy forbids promoting upgrades and names freemium upsells;
    it allows explaining why something is unavailable."""
    msg = await limits.daily_quota_message("anonymous", 100, 3600)
    assert "free Pexafy account" in msg, "the way out is stated"
    assert "http" not in msg, "no link"
    # One sentence about the account, not a pitch spread across the message. The other
    # mention ("without an account") names the situation, it does not sell anything.
    assert msg.count("free Pexafy account") == 1
    for pitch in ("upgrade", "plan", "pro", "sign up now", "unlimited"):
        assert pitch not in msg.lower(), f"reads as a pitch: {pitch}"


@pytest.mark.asyncio
async def test_daily_quota_message_survives_a_junk_retry_after():
    assert "midnight UTC" in await limits.daily_quota_message("anonymous", 100, "soon")


@pytest.mark.asyncio
async def test_an_hour_is_singular():
    assert "1 hour." in await limits.daily_quota_message("anonymous", 100, 3600)


# ── What a spent allowance comes back as ─────────────────────────────────────
# By default, an empty grid carrying the wall (server._wall_as_grid): a conversation
# that has already shown the grid mounts it again and draws the wall, and the model
# reads the `notice`. With PEXAFY_BUDGET_WALL_GRID=0 the refusal is raised as
# linking.BudgetWall and answered at the tool-call boundary as an error result.
def _refusal(code="DAILY_QUOTA_EXCEEDED", **headers):
    import json as _json

    import httpx

    base = {"X-Plan": "anonymous", "X-Daily-Quota-Limit": "100",
            "X-Daily-Quota-Remaining": "0", "Retry-After": "25200"}
    base.update(headers)
    body = _json.dumps({"success": False, "data": None,
                        "error": {"code": code, "message": "nope"}}).encode()
    return httpx.Response(429, headers=base, content=body,
                          request=httpx.Request("GET", "https://api.example/search"))


@pytest.mark.asyncio
async def test_a_spent_day_asks_the_host_to_offer_an_account():
    from unittest import mock

    from pexafy_mcp import linking, server

    with mock.patch.object(server, "WALL_AS_GRID", False), \
         pytest.raises(linking.BudgetWall) as caught:
        await server._surface_plan_limits(_refusal())

    wall = caught.value
    assert "used up" in wall.message
    assert "about 7 hours" in wall.message
    assert wall.signed_in is False   # anonymous: there IS something to offer


@pytest.mark.asyncio
async def test_the_empty_grid_is_what_a_spent_allowance_comes_back_as():
    from pexafy_mcp import budget, server

    response = _refusal()
    with budget.collect_for_grid() as grid:
        await server._surface_plan_limits(response)  # must NOT raise — this is the default

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["budget"]["state"] == "exhausted"
    assert "used up" in body["notice"]
    # The body is what the model reads: the numbers and the sentence. Who is asking,
    # the wall's wording and its button go to the grid, on `_meta`.
    assert "account" not in body
    assert set(body["budget"]) <= set(budget.MODEL_FIELDS)
    assert grid["account"]["state"] == "anonymous"
    assert grid["budget"]["title"] == "Today's free searches are used up"
    assert grid["budget"]["cta_label"] == "Sign in"


@pytest.mark.asyncio
async def test_a_rate_limit_still_raises():
    """It clears in seconds, and "wait, then try once more" is genuinely the right
    answer — which no empty grid can express."""
    from fastmcp.exceptions import ToolError

    from pexafy_mcp import server

    with pytest.raises(ToolError) as caught:
        await server._surface_plan_limits(
            _refusal(code="RATE_LIMITED", **{"Retry-After": "12"}))
    assert "try once more" in str(caught.value)


@pytest.mark.asyncio
async def test_the_wall_is_not_a_tool_error():
    """A ToolError is a message. The wall has to become a RESULT with `_meta` on it —
    the host shows its connect-account prompt for nothing less."""
    from fastmcp.exceptions import FastMCPError, ToolError

    from pexafy_mcp import linking

    assert not issubclass(linking.BudgetWall, ToolError)
    # But it MUST be a FastMCPError: that is the one branch of FastMCP's tool handler
    # that re-raises what it was given, instead of reshaping it into a ToolError before
    # any middleware can see it.
    assert issubclass(linking.BudgetWall, FastMCPError)


@pytest.mark.asyncio
async def test_an_account_at_its_monthly_limit_is_offered_nothing():
    """They are already signed in, so the connect-account prompt would be a dead end —
    and the only thing past a monthly limit is a bigger plan, which is the upgrade the
    platform's rules forbid us to promote."""
    from pexafy_mcp import linking, server

    response = _refusal(code="QUOTA_EXCEEDED", **{
        "X-Plan": "free", "X-Quota-Limit": "5000", "X-Quota-Remaining": "0"})
    del response.headers["X-Daily-Quota-Limit"]
    del response.headers["X-Daily-Quota-Remaining"]
    del response.headers["Retry-After"]

    with mock.patch.object(server, "WALL_AS_GRID", False), \
         pytest.raises(linking.BudgetWall) as caught:
        await server._surface_plan_limits(response)

    assert caught.value.signed_in is True
    _assert_no_sales_pitch(caught.value.message)


@pytest.mark.asyncio
async def test_the_host_prompt_is_written_for_a_person_not_for_the_assistant():
    """The description rides into the host's connect dialog. One that opens with
    "I can't search Pexafy" is the assistant talking to itself in front of a person."""
    from pexafy_mcp import linking, server

    from unittest import mock as _mock

    with _mock.patch.object(server, "WALL_AS_GRID", False), \
         pytest.raises(linking.BudgetWall) as caught:
        await server._surface_plan_limits(_refusal())

    wall = caught.value
    assert wall.prompt.startswith("Today's free searches are used up")
    assert "I can't" not in wall.prompt
    assert "about 7 hours" in wall.prompt
    _assert_no_sales_pitch(wall.prompt)
    # The assistant still gets its own sentence.
    assert wall.message != wall.prompt


@pytest.mark.asyncio
async def test_an_account_out_of_month_is_given_somewhere_to_go():
    """A sentence with no door is a dead end. The one thing the rules allow is a link
    to an informational page about the options — never a checkout, and never a plan
    displayed inside the conversation."""
    from unittest import mock

    from pexafy_mcp import budget, limits

    with mock.patch.object(budget, "OPTIONS_URL", "https://pexafy.com/mcp/limits/"):
        msg = await limits.monthly_quota_message("free", 5000)
    assert "https://pexafy.com/mcp/limits/" in msg
    assert "resets on the 1st" in msg
    _assert_no_sales_pitch(msg)


@pytest.mark.asyncio
async def test_with_no_page_the_sentence_stands_alone():
    from unittest import mock

    from pexafy_mcp import budget, limits

    with mock.patch.object(budget, "OPTIONS_URL", ""):
        msg = await limits.monthly_quota_message("free", 5000)
    assert "http" not in msg


@pytest.mark.asyncio
async def test_the_wall_names_the_way_out_and_leaves_the_choice_to_the_person():
    """The way out is said, and taken only if the person wants it.

    "CALL connect_account immediately" put the host's button on screen, and it did so
    whether or not the person wanted an account: OpenAI forbids a result that attempts
    "to redirect the interaction", and Anthropic a call the user did not request. So the
    wall states what raises the limit, has the person told, and names the tool for
    when they want to connect — the call follows their answer, not the refusal.
    """
    msg = await limits.daily_quota_message("anonymous", 100, 3600, host_can_connect=True)
    assert "A free Pexafy account raises the limit." in msg
    assert "Tell the person in one short sentence" in msg
    assert "if they want to connect an account, call connect_account" in msg
    assert "immediately" not in msg and "CALL" not in msg
    assert "Do not retry" in msg
    _assert_no_sales_pitch(msg)


@pytest.mark.asyncio
async def test_a_host_that_shows_no_prompt_gets_the_fact_alone():
    """Claude Code without an account: connect_account connects nothing there — "Claude
    passes the error text to the model as the tool result and moves on, and there is no
    auth prompt" — so the wall names no tool, and gives no order about what to say."""
    msg = await limits.daily_quota_message("anonymous", 100, 3600)   # the default
    assert msg == (
        "Pexafy refused this search: the 100 daily searches available without an account "
        "are used up. The allowance resets in about 1 hour. A free Pexafy account raises "
        "the limit. Do not retry the search.")
    assert await limits.daily_quota_message(
        "anonymous", 100, 3600, host_can_connect=False) == msg
    # With no connect tool registered, the host does not matter: the same fact.
    with mock.patch.object(limits.linking, "ENABLED", False):
        assert await limits.daily_quota_message(
            "anonymous", 100, 3600, host_can_connect=True) == msg


@pytest.mark.asyncio
@pytest.mark.parametrize(("openai", "named"), [(True, True), (False, False)])
async def test_the_wall_is_worded_for_the_host_that_asked(openai, named):
    """Read where the message is built, inside the tool call: the empty grid's `notice`
    is what the model reads."""
    from pexafy_mcp import server

    response = _refusal()
    with mock.patch.object(server.hosts, "is_openai", return_value=openai):
        await server._surface_plan_limits(response)
    notice = response.json()["notice"]
    assert ("call connect_account" in notice) is named
    assert ("Tell the person" in notice) is named
    assert notice.endswith("Do not retry the search.")


@pytest.mark.asyncio
async def test_a_signed_in_wall_never_calls_the_connect_tool():
    """They are through that door already; the tool would send them round it again."""
    from unittest import mock

    from pexafy_mcp import budget

    with mock.patch.object(budget, "OPTIONS_URL", "https://pexafy.com/mcp/limits/"):
        msg = await limits.monthly_quota_message("free", 5000)
    assert "connect_account" not in msg
    assert "https://pexafy.com/mcp/limits/" in msg
