"""What the assistant is told when a plan limit stops a search.

These messages are a report from Pexafy, in the third person ("Pexafy refused this
search: …"): the server does not speak as the assistant, nor write the assistant's
lines for it. They state a fact and stop there. OpenAI's plugin policy forbids the rest:
"Plugins must not display subscription plans, initiate new subscriptions, or promote
upgrades", and it names freemium upsells as a form of selling subscriptions. The same
policy allows the useful half — "the plugin may explain that [a feature requires a
different plan]. This information should help users understand why the feature is
unavailable" — so the message gives the limit, the number and the plan it belongs to.

Numbers come from the API's response headers (X-Plan, X-Quota-Limit,
X-Daily-Quota-Limit, X-RateLimit-Limit, Retry-After), and for the key limit from
Django's answer to the token resolution, so none of this costs an extra call.
"""
from __future__ import annotations

import os

from . import budget, linking

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
    return f"the {plan.title()} plan" if plan else "this account's plan"


# ── Key limit ────────────────────────────────────────────────────────────────
# Two details this message has to get right. It says "API keys", the words on the page
# it links to, not "connectors" — a caller sent to look for connectors finds a list of
# API keys. And it does not say "then reconnect": the server resolves the OAuth token
# against Django on every request with no cache, so the moment a key slot frees up the
# next question works. Telling someone to tear down an OAuth connection they never had
# to touch is expensive advice.
def key_limit_message(ctx: dict | None = None) -> str:
    ctx = ctx or {}
    label = str(ctx.get("plan_label") or "").strip()
    plan = f"this account's {label} plan" if label else "this account's plan"
    mx = ctx.get("max")
    head = (
        f"Pexafy refused this request: connecting an assistant uses an API key, and "
        f"{plan} allows {_keys(mx)}, {'already' if _n(mx) == '1' else 'all'} in use."
        if mx is not None else
        f"Pexafy refused this request: {plan} has reached its API key limit."
    )
    return (f"{head} Revoking a key that is no longer used, at {CONNECTORS_URL}, makes "
            f"room for this connection: the next request then works, with nothing to "
            f"reconnect.")


# ── Monthly quota ────────────────────────────────────────────────────────────
async def monthly_quota_message(plan: str = "", limit=None) -> str:
    allowance = f"all {_n(limit)} of this month's searches" if limit else "all of this month's searches"
    head = f"Pexafy refused this search: {allowance} on {_plan_phrase(plan)} are used."
    # Somebody who has run out of a month has nowhere to go from a sentence. The one
    # thing the platform's rules allow is a link to an informational page describing
    # the options — not a checkout, not a plan displayed here — so when such a page is
    # configured, name it. Without one, the sentence stands alone.
    #
    # No connect_account here: they are already signed in, and the tool would send them
    # round a door they are through. A page describing the options is the one thing the
    # platform's rules allow instead.
    if budget.OPTIONS_URL:
        return (f"{head} The allowance resets on the 1st. The options are described at "
                f"{budget.OPTIONS_URL}. Do not retry the search.")
    return f"{head} The allowance resets on the 1st. Do not retry the search."


# ── Daily quota, for a caller with no account ────────────────────────────────
# Its own message, and not the monthly one. A daily budget comes back, and saying so is
# the useful half: an assistant told "you have used this month's searches" stops asking
# for the rest of the day, when the answer is "tomorrow, or sign in".
#
# The sign-in half stays a statement of fact. OpenAI's plugin policy forbids promoting
# upgrades and names freemium upsells explicitly, while allowing the explanation of why
# something is unavailable — so this says what the limit is and what lifts it, once,
# without a link or a pitch.
async def daily_quota_message(plan: str = "", limit=None, retry_after=None, *,
                              host_can_connect: bool = False) -> str:
    """`plan` is unused: a daily allowance only exists without an account, so there is
    no plan to name. It keeps the call shape of the two other messages.

    `host_can_connect` is whether the calling host shows its own account-connection
    prompt when connect_account asks it to — ChatGPT does (the caller reads it from
    hosts.is_openai). Passed in rather than looked up here, so this stays a function of
    its arguments."""
    allowance = f"the {_n(limit)} daily searches" if limit else "today's searches"
    head = f"Pexafy refused this search: {allowance} available without an account are used up."
    try:
        hours = max(1, round(int(retry_after) / 3600))
        when = f" The allowance resets in about {hours} hour{'s' if hours > 1 else ''}."
    except (TypeError, ValueError):
        when = " The allowance resets at midnight UTC."
    # The grid draws the wall only in a conversation that has already shown it
    # (server._wall_as_grid); anywhere else this sentence is all the person gets, so it
    # has to carry the way out as well as the fact.
    #
    # The way out is named, and taken only if the person wants it. An order to call
    # connect_account at once redirected the conversation ("attempt to redirect the
    # interaction", OpenAI) and called a tool nobody asked for (Anthropic); the person
    # is told, and the connection follows their answer.
    if linking.ENABLED and host_can_connect:
        return (f"{head}{when} A free Pexafy account raises the limit. Tell the person in "
                f"one short sentence; if they want to connect an account, call "
                f"{linking.CONNECT_TOOL}. Do not retry the search.")
    # The fact alone. With no connect tool registered there is nothing to call; and a
    # host that does not show the prompt (Claude Code without an account) would be sent
    # to a tool that connects nothing there — "Claude passes the error text to the model
    # as the tool result and moves on, and there is no auth prompt" (Anthropic). How to
    # tell the person is left to the model, as everywhere else.
    return f"{head}{when} A free Pexafy account raises the limit. Do not retry the search."


# ── Rate limit ───────────────────────────────────────────────────────────────
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


def _rate_owner(plan: str) -> str:
    """Whose pace the rate limit is: a plan's, or, for a caller with no account — who
    has no plan to name — the allowance without an account, the words every other text
    uses for them."""
    if (plan or "").strip().lower() == budget.ANON_PLAN:
        return "the allowance without an account"
    return _plan_phrase(plan)


async def rate_limit_message(plan: str = "", rate_limit=None, retry_after=None) -> str:
    cap = f" ({_n(rate_limit)} searches per minute)" if rate_limit else ""
    head = (f"Pexafy refused this search: searches are arriving faster than "
            f"{_rate_owner(plan)} allows{cap}.")
    # The wait stays: without it an assistant retries in a tight loop, which only
    # burns more of the per-minute budget and never recovers.
    return f"{head} {_wait_phrase(retry_after)}."
