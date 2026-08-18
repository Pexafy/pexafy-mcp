"""What the assistant says when a plan limit stops a search.

Every assertion here is really one rule, read from OpenAI's plugin policy:
"Plugins must not display subscription plans, initiate new subscriptions, or
promote upgrades", while "the plugin may explain that [a feature requires a
different plan]". So each message must carry the fact and nothing that sells:
no tier name, no price, no link to pricing.

The previous version of this file asserted the opposite — that the copy named the
next tier and linked to the pricing page — which is how the rule was found to be
broken.
"""
from __future__ import annotations

import pytest

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


def test_connector_pluralisation():
    assert limits._conn(1) == "1 connector"
    assert limits._conn(3) == "3 connectors"


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


def test_connector_limit_points_at_the_user_s_own_keys():
    """Managing your own connectors is not an upsell — it is the way out."""
    message = limits.key_limit_message({"plan_label": "Free", "max": 1})
    assert "1 connector" in message
    assert limits.CONNECTORS_URL in message
    _assert_no_sales_pitch(message)


def test_connector_limit_without_a_known_maximum():
    message = limits.key_limit_message({})
    assert "connector limit" in message
    _assert_no_sales_pitch(message)
