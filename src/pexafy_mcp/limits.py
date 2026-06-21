"""Sales-oriented, metric-driven limit messages surfaced in-chat (Claude).

When a user hits a plan limit, we return a precise upgrade nudge as the MCP tool
error so it appears directly in the assistant's chat: what's blocked (exact N/N),
what the next plan offers, its price, and the pricing link.

Numbers come straight from the API's response headers (X-Plan, X-Quota-Limit,
X-RateLimit-Limit) — no extra usage call. The next-tier copy reads the public
plans list ({API}/api/v1/billing/plans), cached briefly. Copy lives here.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

PRICING_URL = os.environ.get("PEXAFY_PRICING_URL", "https://pexafy.com/pricing/")
CONNECTORS_URL = os.environ.get("PEXAFY_CONNECTORS_URL", "https://pexafy.com/dashboard/api-keys/")
_API_BASE = os.environ.get("PEXAFY_API_BASE_URL", "http://localhost:8000").rstrip("/")

DIM_MONTHLY = "requests_per_month"
DIM_RATE = "rate_limit_per_min"

_plans_cache: list[dict] = []
_plans_at: float = 0.0
_PLANS_TTL = 3600


def _n(v) -> str:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return str(v)
    return "unlimited" if v == 0 else f"{v:,}"


def _conn(v) -> str:
    s = _n(v)
    return f"{s} connector" if s == "1" else f"{s} connectors"


async def _plans() -> list[dict]:
    global _plans_cache, _plans_at
    if _plans_cache and (time.time() - _plans_at) < _PLANS_TTL:
        return _plans_cache
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{_API_BASE}/api/v1/billing/plans")
            r.raise_for_status()
            data = r.json()
        plans = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(plans, list) and plans:
            _plans_cache, _plans_at = plans, time.time()
    except Exception as exc:  # noqa: BLE001 — copy still works without the ladder
        logger.warning("Could not fetch plans for sales copy: %s", exc)
    return _plans_cache


def _limit(p: dict, dim: str) -> int:
    return int((p.get("limits") or {}).get(dim, 0) or 0)


def _label(p: dict) -> str:
    return p.get("display_name") or p.get("name", "").title()


def _price(p: dict) -> str:
    cents = (p.get("price") or {}).get("monthly_cents", 0)
    return "free" if not cents else f"€{cents // 100}/mo"


def _next_plan(plans: list[dict], current: str, dim: str) -> dict | None:
    names = [p.get("name") for p in plans]
    if current not in names:
        return None
    cur = _limit(plans[names.index(current)], dim)
    for p in plans[names.index(current) + 1:]:
        lim = _limit(p, dim)
        if (lim == 0 and cur != 0) or (cur != 0 and lim > cur):
            return p
    return None


def _plan_phrase(plan: str) -> str:
    return f"the {plan.title()} plan" if plan else "your plan"


# ── Key limit: Django already supplies the full metric context in `ctx` ──────
def key_limit_message(ctx: dict | None = None) -> str:
    ctx = ctx or {}
    label = ctx.get("plan_label") or "your"
    mx = ctx.get("max")
    in_use = "it's already in use" if _n(mx) == "1" else "they're all in use"
    head = (
        f"I can't search Pexafy right now: your {label} plan includes {_conn(mx)}, and {in_use}."
        if mx is not None else
        f"I can't search Pexafy right now: you've reached your {label} plan's connector limit."
    )
    if ctx.get("next_plan"):
        upgrade = (f"upgrade to {ctx['next_plan']} ({ctx.get('next_price', 'see pricing')}) "
                   f"for {_conn(ctx.get('next_max'))} — {PRICING_URL}")
    else:
        upgrade = f"upgrade your plan for more connectors — {PRICING_URL}"
    return (f"{head} Two quick ways forward: (1) {upgrade}; or "
            f"(2) free up a connector you're not using — {CONNECTORS_URL} — then reconnect.")


# ── Monthly quota / rate limit: numbers from headers, next tier from plans ───
async def monthly_quota_message(plan: str = "", limit=None) -> str:
    allowance = f"all {_n(limit)} of this month's searches" if limit else "all of this month's searches"
    head = f"I can't search Pexafy: you've used {allowance} on {_plan_phrase(plan)}."
    nxt = _next_plan(await _plans(), plan, DIM_MONTHLY)
    if nxt:
        return (f"{head} Upgrade to {_label(nxt)} ({_price(nxt)}) for "
                f"{_n(_limit(nxt, DIM_MONTHLY))} searches a month and keep going — {PRICING_URL}")
    return f"{head} Upgrade for a higher monthly allowance — {PRICING_URL}"


def _wait_phrase(retry_after) -> str:
    """How long to wait before a SINGLE retry — never a tight loop (which only
    burns more of the per-minute budget and never recovers)."""
    try:
        secs = max(1, int(retry_after))
    except (TypeError, ValueError):
        secs = None
    if secs:
        return f"Wait {secs}s, then try once more"
    return "Wait a few seconds, then try once more (don't retry in a loop)"


async def rate_limit_message(plan: str = "", rate_limit=None, retry_after=None) -> str:
    cap = f" ({_n(rate_limit)} searches per minute)" if rate_limit else ""
    head = f"I can't search Pexafy: you're searching faster than {_plan_phrase(plan)} allows{cap}."
    wait = _wait_phrase(retry_after)
    nxt = _next_plan(await _plans(), plan, DIM_RATE)
    if nxt:
        return (f"{head} {wait}, or upgrade to {_label(nxt)} "
                f"({_price(nxt)}) for {_n(_limit(nxt, DIM_RATE))} searches/min — {PRICING_URL}")
    return f"{head} {wait}, or upgrade for a higher rate — {PRICING_URL}"
