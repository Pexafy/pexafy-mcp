"""What the assistant says, in chat, when a plan limit stops a search.

These used to be upgrade nudges: the next tier, its price, a link to the pricing
page. OpenAI's plugin policy forbids exactly that — "Plugins must not display
subscription plans, initiate new subscriptions, or promote upgrades", and names
freemium upsells as a form of selling subscriptions.

The same policy says what is allowed, and it is the useful half: "the plugin may
explain that [a feature requires a different plan]. This information should help
users understand why the feature is unavailable." So the message states the fact —
the limit, the number, the plan it belongs to — and stops there. Someone told they
have used all 5,000 searches on the Free plan has learned what they need to know;
they do not need to be sold to inside their own conversation.

Numbers come straight from the API's response headers (X-Plan, X-Quota-Limit,
X-RateLimit-Limit), so none of this costs an extra call.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

CONNECTORS_URL = os.environ.get("PEXAFY_CONNECTORS_URL", "https://pexafy.com/dashboard/api-keys/")


def _n(v) -> str:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return str(v)
    return "unlimited" if v == 0 else f"{v:,}"


def _keys(v) -> str:
    s = _n(v)
    return f"{s} API key" if s == "1" else f"{s} API keys"


def _plan_phrase(plan: str) -> str:
    return f"the {plan.title()} plan" if plan else "your plan"


# ── Key limit: Django already supplies the full metric context in `ctx` ──────
# Two things this message used to get wrong, both of which cost the user real
# effort. It called the keys "connectors", a word that appears nowhere on the page
# it sends them to — they arrive looking for a list of connectors and find a list
# of API keys. And it ended with "then reconnect", which is simply not true: the
# server resolves the OAuth token against Django on every single request, with no
# cache, so the moment a key slot frees up the next question just works. Telling
# someone to tear down and redo an OAuth connection they did not need to touch is
# the most expensive sentence we could have written.
def key_limit_message(ctx: dict | None = None) -> str:
    ctx = ctx or {}
    label = ctx.get("plan_label") or "your"
    mx = ctx.get("max")
    head = (
        f"I can't search Pexafy right now: connecting an assistant uses an API key, "
        f"and your {label} plan's {_keys(mx)} "
        f"{'is' if _n(mx) == '1' else 'are'} already in use."
        if mx is not None else
        f"I can't search Pexafy right now: your {label} plan's API key limit is reached."
    )
    return (f"{head} Revoke a key you no longer use at {CONNECTORS_URL} and ask me "
            f"again — the connection repairs itself, there is nothing to reconnect.")


# ── Monthly quota / rate limit: numbers from headers, next tier from plans ───
async def monthly_quota_message(plan: str = "", limit=None) -> str:
    allowance = f"all {_n(limit)} of this month's searches" if limit else "all of this month's searches"
    return f"I can't search Pexafy: you've used {allowance} on {_plan_phrase(plan)}."


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
    # The wait stays: without it an assistant retries in a tight loop, which only
    # burns more of the per-minute budget and never recovers.
    return f"{head} {_wait_phrase(retry_after)}."
