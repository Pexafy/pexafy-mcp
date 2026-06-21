"""Plan-limit message formatting (numbers in, sales copy out)."""
from __future__ import annotations

from pexafy_mcp import limits

PLANS = [
    {"name": "free", "display_name": "Free", "price": {"monthly_cents": 0},
     "limits": {"requests_per_month": 1000, "rate_limit_per_min": 10}},
    {"name": "starter", "display_name": "Starter", "price": {"monthly_cents": 900},
     "limits": {"requests_per_month": 10000, "rate_limit_per_min": 60}},
    {"name": "pro", "display_name": "Pro", "price": {"monthly_cents": 2900},
     "limits": {"requests_per_month": 0, "rate_limit_per_min": 0}},  # 0 = unlimited
]


def test_number_formatting():
    assert limits._n(0) == "unlimited"
    assert limits._n(1500) == "1,500"
    assert limits._n(None) == "None"


def test_connector_pluralisation():
    assert limits._conn(1) == "1 connector"
    assert limits._conn(3) == "3 connectors"


def test_price_and_label():
    assert limits._price(PLANS[0]) == "free"
    assert limits._price(PLANS[1]) == "€9/mo"
    assert limits._label(PLANS[2]) == "Pro"


def test_next_plan_picks_the_next_higher_tier():
    nxt = limits._next_plan(PLANS, "free", limits.DIM_MONTHLY)
    assert nxt["name"] == "starter"


def test_next_plan_treats_zero_as_unlimited_upgrade():
    nxt = limits._next_plan(PLANS, "starter", limits.DIM_MONTHLY)
    assert nxt["name"] == "pro"  # 0 (unlimited) beats a finite current limit


def test_next_plan_none_when_already_top():
    assert limits._next_plan(PLANS, "pro", limits.DIM_MONTHLY) is None


def test_key_limit_message_mentions_pricing_and_connectors():
    msg = limits.key_limit_message({"plan_label": "Free", "max": 1})
    assert "connector" in msg
    assert limits.PRICING_URL in msg


async def test_monthly_quota_message_uses_next_tier(monkeypatch):
    async def fake_plans():
        return PLANS

    monkeypatch.setattr(limits, "_plans", fake_plans)
    msg = await limits.monthly_quota_message("free", "1000")
    assert "1,000" in msg
    assert "Starter" in msg
    assert limits.PRICING_URL in msg


async def test_rate_limit_message_tells_user_to_wait(monkeypatch):
    async def fake_plans():
        return PLANS

    monkeypatch.setattr(limits, "_plans", fake_plans)
    msg = await limits.rate_limit_message("free", "10", "5")
    assert "Wait 5s" in msg
    assert "Starter" in msg


async def test_messages_degrade_without_a_plan_ladder(monkeypatch):
    async def empty_plans():
        return []

    monkeypatch.setattr(limits, "_plans", empty_plans)
    msg = await limits.monthly_quota_message("free", "1000")
    # No ladder available → still a coherent upgrade nudge, no crash.
    assert limits.PRICING_URL in msg
