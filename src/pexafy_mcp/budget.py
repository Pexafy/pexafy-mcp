"""Who is asking, what is left of their allowance, and what may be said about it.

The API answers `X-Plan` on every request, `X-Daily-Quota-Limit` /
`X-Daily-Quota-Remaining` to a caller with no account, and `X-Quota-Limit` /
`X-Quota-Remaining` to one with an account. Read on every answer, they let a caller
hear about the budget before the refusal — the one moment where hearing about it is
no longer useful.

This module turns those headers into three things the grid draws:

  `account(headers)`  who is asking — present on every answer, because the head's
                      account button is present on every answer. Anonymous callers
                      get the way in; signed-in callers get a state, not a pitch.
  `read(headers)`     what is left, ONCE it is worth saying — silence below the
                      threshold, for both the daily allowance and the monthly one.
  `wall(headers)`     the screen that replaces an empty grid when the allowance is
                      gone, so a refusal arrives as a page rather than as a sentence
                      that scrolled past.

The plan's NAME is read (it is how a caller with an account is told from one without)
and never sent: nothing draws it, and a result carries only what the request needs
("only data that is directly relevant to the user's request", OpenAI's guidelines).

**Two readers, two halves.** The model reads the numbers of the allowance and one
sentence (`message`, MODEL_FIELDS): enough to say how many searches are left and what
lifts the limit, and all a host without a UI ever shows. The rest is the grid's — the
account block, the panel's two lines, the button's label and where it goes — and rides
on the result's `_meta` under ACCOUNT_META_KEY, the half of an answer a host hands to
the frame and keeps from the model (`for_grid`, `keep_for_grid`). A sign-in link given
to the model would be worse than noise besides: signing in on the site creates an
account without attaching it to the connector, so the model repeating the link lifts
no limit. `connect_account` does.

**What may be said, and why it is worded like this.** OpenAI's app guidelines forbid
selling digital products or services — "including subscriptions, digital content,
tokens, or credits … whether offered directly or indirectly (for example, through
freemium upsells)" — and forbid displaying subscription plans, initiating
subscriptions, promoting upgrades, or linking to a checkout. They permit explaining
why something is unavailable, permit signing in to an existing account, and permit
linking to an informational page describing available plans.

So this module says two things and stops: how much is left, and — for a caller with
no account — that a free account lifts the limit. No price, no plan name, no
checkout, no argument for buying anything.

**The signed-in caller gets LESS, not more.** At the end of a paid or free plan's
month there is nothing we are allowed to offer: a larger plan is exactly the upgrade
the guidelines forbid promoting, and the dashboard we would link to carries its
buttons. So that screen states the fact and the reset date, and offers nothing. The
asymmetry is deliberate — creating a free account sells nothing, changing plan does.

**Three destinations.** Somebody who wants to sign in wants the sign-in form, and
somebody already signed in has no use for it, nor for a page about the product.

    sign_in_url   /auth/login/ — for a visitor. Signing in to an account is the one
                  thing the guidelines name as permitted, and the page it opens
                  offers creating a free one, which sells nothing.
    home_url      the site — for a signed-in caller with nothing to be told. Their
                  button is a way back to Pexafy, not a sales channel.
    options_url   an informational page about allowances — for a signed-in caller who
                  has run out. The guidelines permit "link[ing] to an informational
                  page describing available plans or entitlement options"; they forbid
                  linking to a checkout or to a page that starts a purchase. So this
                  points at the description, never at the till, and it is EMPTY by
                  default: no page, no link.

    PEXAFY_BUDGET_NOTICE          1   attach the notice at all
    PEXAFY_BUDGET_WARN_AT       0.8   share of the allowance that triggers the warning
    PEXAFY_BUDGET_ACCOUNT_CTA     1   offer the way in / the way to read about limits
    PEXAFY_SIGNIN_URL                 sign-in form (default: PEXAFY_WEB_URL/auth/login/)
    PEXAFY_HOME_URL                   the signed-in caller's way back (default: PEXAFY_WEB_URL/)
    PEXAFY_WEB_URL                    the site this server belongs to
    PEXAFY_ACCOUNT_OPTIONS_URL        informational page about allowances (default: none)

Turning the CTA off leaves the counter, which is pure fact and defensible anywhere.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os

from .env import flag

logger = logging.getLogger("pexafy.mcp.budget")

LIMIT_HEADER = "X-Daily-Quota-Limit"
REMAINING_HEADER = "X-Daily-Quota-Remaining"
MONTH_LIMIT_HEADER = "X-Quota-Limit"
MONTH_REMAINING_HEADER = "X-Quota-Remaining"
PLAN_HEADER = "X-Plan"
RETRY_HEADER = "Retry-After"

# The plan name the API gives a caller who has no account. Anything else is somebody
# with an account, whatever it is called, and gets the signed-in wording.
ANON_PLAN = "anonymous"

ENABLED = flag("PEXAFY_BUDGET_NOTICE", True)
ACCOUNT_CTA = flag("PEXAFY_BUDGET_ACCOUNT_CTA", True)

# The site this server belongs to — the same variable the footer link reads, so a
# preprod server never sends a reader to production.
WEB_URL = os.environ.get("PEXAFY_WEB_URL", "https://pexafy.com").rstrip("/")
# The sign-in form itself, not a page about the product: a button labelled "Sign in"
# has to open onto the form.
SIGN_IN_URL = os.environ.get("PEXAFY_SIGNIN_URL", "").strip() or f"{WEB_URL}/auth/login/"
HOME_URL = os.environ.get("PEXAFY_HOME_URL", "").strip() or f"{WEB_URL}/"
# Where a signed-in caller reads about allowances. Empty by default, and a link that
# does not exist is not drawn: this one has to point at a page that describes options
# without starting a purchase, and until such a page exists there is no honest link.
# `.strip()`, not `.rstrip("/")`: these are page URLs, and Django answers a missing
# trailing slash with a 301. One redirect on every press of the button, through a
# host's own link opener, for a slash we removed ourselves.
OPTIONS_URL = os.environ.get("PEXAFY_ACCOUNT_OPTIONS_URL", "").strip()

try:
    WARN_AT = float(os.environ.get("PEXAFY_BUDGET_WARN_AT", "0.8"))
except ValueError:
    logger.warning("PEXAFY_BUDGET_WARN_AT is not a number — using 0.8")
    WARN_AT = 0.8

# The one word on the button. "Sign in", not "Create an account": the guidelines
# explicitly permit signing in to an existing account, and the page it opens offers
# both anyway. A label is a label — it is not where the argument is made.
SIGN_IN_LABEL = "Sign in"
# What a signed-in caller's button says when they have run out. Not "Upgrade", not
# "See plans": the guidelines forbid promoting an upgrade and forbid displaying
# subscription plans, and permit describing entitlement OPTIONS on a page of our own.
# The label names that page and makes no argument.
OPTIONS_LABEL = "Account options"

# ── Which half of an answer each part belongs to ─────────────────────────────
# The fields of a budget block the model reads (see the module docstring). Everything
# else in a block — `short`, `title`, `detail`, `cta_label`, `account_url` — and the
# whole `account` block are the grid's, on `_meta`.
MODEL_FIELDS = ("scope", "limit", "remaining", "used", "state", "signed_in", "message")

# The `_meta` key the grid reads them under: {"account": …, "budget": …}, either one
# absent when there is nothing to say. Namespaced like the rest of that channel.
ACCOUNT_META_KEY = "pexafy/account"


def for_model(block: dict | None) -> dict | None:
    """The half of a budget block the model reads; None stays None."""
    if not isinstance(block, dict):
        return block
    return {key: block[key] for key in MODEL_FIELDS if key in block}


def for_grid(block: dict | None) -> dict | None:
    """The half of a budget block only the grid draws, or None when there is none."""
    if not isinstance(block, dict):
        return None
    rest = {key: value for key, value in block.items() if key not in MODEL_FIELDS}
    return rest or None


# The grid's half of the tool call in progress. It is worked out in the HTTP response
# hooks (server._enrich_response, server._wall_as_grid), several layers below the only
# code that can write a result's `_meta`, the MCP middleware (origin.AttachOrigin): a
# ContextVar connects the two, as anonymous.current does the other way round. It holds a
# dict the middleware opened rather than a value, so that a hook run in a copied context
# still writes where the middleware reads.
_grid_half: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "pexafy_grid_half", default=None)


@contextlib.contextmanager
def collect_for_grid():
    """Open the grid's half of one tool call; what `keep_for_grid` hands it lands here."""
    holder: dict = {}
    token = _grid_half.set(holder)
    try:
        yield holder
    finally:
        _grid_half.reset(token)


def keep_for_grid(*, account: dict | None = None, budget: dict | None = None) -> None:
    """Hand the grid its half of this answer: who is asking, and the panel's wording.

    Outside a tool call (a unit call, the probe run on its own) nobody is collecting,
    and it is dropped: it was only ever the grid's.
    """
    holder = _grid_half.get()
    if holder is None:
        return
    if account:
        holder["account"] = account
    if budget:
        holder["budget"] = budget


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _signed_in(headers) -> bool:
    plan = (headers.get(PLAN_HEADER) or "").strip().lower()
    # No plan header at all: assume anonymous. The button then offers the way in,
    # which is wrong for nobody — a signed-in caller who sees it is offered a page
    # they are already past, not a purchase.
    return bool(plan) and plan != ANON_PLAN


def _allowance(headers) -> tuple[str, int, int] | None:
    """Which allowance is running out, and where it stands: (scope, limit, remaining).

    The daily one first, because it is the one a caller without an account actually
    hits — the monthly allowance of the anonymous service plan is an implementation
    detail they never reach. `None` when no allowance is measurable, which is normal:
    an unmetered plan answers `-1`.

    The two headers are not read the same way, and the difference is not an oversight.
    A negative DAILY remaining is a stale count and is clamped to zero here — "-2
    searches left" must never reach a screen, but neither must the notice vanish at the
    exact moment it matters. A negative MONTHLY remaining is the API's documented
    sentinel for an unmetered plan (`X-Quota-Remaining: -1`), and clamping that to zero
    would put a wall in front of somebody who has no limit at all. Either way the
    remaining count returned is never negative.
    """
    limit = _int(headers.get(LIMIT_HEADER))
    remaining = _int(headers.get(REMAINING_HEADER))
    if limit and limit > 0 and remaining is not None:
        return "day", limit, max(0, remaining)

    limit = _int(headers.get(MONTH_LIMIT_HEADER))
    remaining = _int(headers.get(MONTH_REMAINING_HEADER))
    if limit and limit > 0 and remaining is not None and remaining >= 0:
        return "month", limit, remaining
    return None


def _period(scope: str) -> str:
    return "today" if scope == "day" else "this month"


def _reset_phrase(scope: str, headers=None) -> str:
    """When the allowance comes back, in the caller's terms.

    `Retry-After` is only on a refusal, and only the daily one carries a real value
    (the monthly refusal has nothing to wait for that is worth counting in seconds),
    so the fallback is the rule itself rather than a computed date we would have to
    keep in step with the API's own reset.
    """
    if scope == "day":
        hours = _reset_hours(scope, headers)
        if hours:
            return f"It resets in about {hours} hour{'s' if hours > 1 else ''}."
        return "It resets at midnight UTC."
    return "It resets at the start of next month."


def _reset_hours(scope: str, headers=None) -> int | None:
    """The hours until a daily allowance comes back, when a refusal says (Retry-After)."""
    if scope != "day":
        return None
    secs = _int((headers or {}).get(RETRY_HEADER))
    if secs and secs > 0:
        return max(1, round(secs / 3600))
    return None


def _wording_facts(scope: str, headers=None) -> dict:
    """What the grid needs to say the panel in the reader's language rather than in
    the English above: the hours to the reset, and whether the free account is
    offered. Numbers and a flag — the sentences are the grid's (budgetWords)."""
    return {"reset_hours": _reset_hours(scope, headers), "free_account": ACCOUNT_CTA}


# ── Who is asking ────────────────────────────────────────────────────────────
def account(headers) -> dict | None:
    """The head's account button: a state, and — for a visitor — the way in.

    On every answer's `_meta`, unlike the notice. The button is part of the grid's
    furniture rather than an alarm: a reader who wants to sign in should not have to
    spend 80% of an allowance to find out where. The model is not told: see
    ACCOUNT_META_KEY.
    """
    # No plan header: not an answer from the API (a proxy's page, a unit call), and
    # nothing is known about who is asking.
    if not (headers.get(PLAN_HEADER) or "").strip():
        return None
    if _signed_in(headers):
        # Already in, so there is nothing to offer and no panel worth opening: the
        # button becomes a way back to the site. What it must NOT become is a sales
        # channel — see the module docstring.
        return {"state": "signed_in", "label": "Signed in", "home_url": HOME_URL}

    # A visitor is offered the way in on every single answer, allowance or not. This
    # is not the notice: it is the door, and a door is not conditional on running low.
    block = {"state": "anonymous", "label": "Sign in to Pexafy"}
    if ACCOUNT_CTA and SIGN_IN_URL:
        block["sign_in_label"] = SIGN_IN_LABEL
        block["sign_in_url"] = SIGN_IN_URL
    return block


# ── What is left ─────────────────────────────────────────────────────────────
def read(headers) -> dict | None:
    """The budget block for a response, or None when there is nothing to say.

    None in three cases, all of them normal: the feature is off, the caller's plan
    has no measurable allowance, or they are nowhere near the end of it. Saying
    nothing is the default — a counter on every single answer would be noise, and
    noise is what gets ignored at the moment it matters.
    """
    if not ENABLED:
        return None

    found = _allowance(headers)
    if found is None:
        return None
    scope, limit, remaining = found

    used = max(0, limit - remaining)
    # The threshold is on what is SPENT, not on what is left, so that it fires at the
    # same place whatever the allowance: 80 of 100 and 8 of 10 are the same moment.
    if used < WARN_AT * limit:
        return None

    signed_in = _signed_in(headers)
    state = "exhausted" if remaining <= 0 else "warning"
    block = {
        "scope": scope,
        "limit": limit,
        "remaining": remaining,
        "used": used,
        # Three forms, because three surfaces have different room. `message` is the
        # sentence the model reads, and says. `short` is what fits in the corner of a
        # card: rendered as a paragraph it took three lines and pushed the header out
        # of shape. `title`/`detail` are the two lines of the panel the account button
        # opens. Those last three, and the link below, go to the grid alone (for_grid).
        "short": short(remaining, scope),
        "title": short(remaining, scope),
        "detail": _detail(scope, signed_in, headers),
        "state": state,
        "signed_in": signed_in,
        "message": message(limit, remaining, scope, signed_in),
    }
    block.update(_link(signed_in))
    block.update(_wording_facts(scope, headers))
    return block


def _link(signed_in: bool) -> dict:
    """The one button a panel or a wall carries, and where it goes.

    A visitor gets the sign-in form. A signed-in caller gets the page that describes
    allowances, if one is configured — "link to an informational page describing
    available plans or entitlement options" is permitted, "link to a checkout or to a
    page that explicitly initiates the process to upgrade" is not, and the difference
    is the page at the other end, not the label on this side.
    """
    if not ACCOUNT_CTA:
        return {"cta_label": ""}
    if signed_in:
        if not OPTIONS_URL:
            return {"cta_label": ""}
        return {"cta_label": OPTIONS_LABEL, "account_url": OPTIONS_URL}
    if not SIGN_IN_URL:
        return {"cta_label": ""}
    return {"cta_label": SIGN_IN_LABEL, "account_url": SIGN_IN_URL}


def _detail(scope: str, signed_in: bool, headers=None) -> str:
    """The second line of the panel: what happens next.

    For a visitor that is the free account, stated once. For a signed-in caller it is
    the reset and nothing else — the only other thing we could offer is a bigger
    plan, and promoting one is what the guidelines forbid.
    """
    if signed_in:
        return _reset_phrase(scope, headers)
    if ACCOUNT_CTA:
        which = "daily" if scope == "day" else "monthly"
        return f"A free Pexafy account lifts this {which} limit."
    return _reset_phrase(scope, headers)


def short(remaining: int, scope: str = "day") -> str:
    """Four words at most — a status line, not a sentence."""
    if remaining <= 0:
        return "Daily limit reached" if scope == "day" else "Monthly limit reached"
    period = _period(scope)
    if remaining == 1:
        return f"1 search left {period}"
    return f"{remaining:,} searches left {period}"


def message(limit: int, remaining: int, scope: str = "day", signed_in: bool = False) -> str:
    """One sentence, in the caller's own terms: how many searches are left.

    Not "80% used". A share is a statistic; "3 searches left today" is something the
    assistant can act on — by asking a better question with the ones that remain.
    """
    period = _period(scope)
    suffix = "" if signed_in else " without an account"
    if remaining <= 0:
        head = f"That was the last of {period}'s {limit:,} searches{suffix}."
    elif remaining == 1:
        head = f"1 search left {period}{suffix}."
    else:
        head = f"{remaining:,} searches left {period}{suffix}."

    if ACCOUNT_CTA and not signed_in:
        # States what lifts the limit. No price, no plan, no checkout: what the
        # guidelines allow is explaining why something is unavailable, and this is
        # that sentence and nothing more.
        which = "daily" if scope == "day" else "monthly"
        return f"{head} A free Pexafy account lifts this {which} limit."
    return head


# ── The wall ─────────────────────────────────────────────────────────────────
def wall(headers) -> dict:
    """The screen that stands in for a grid that could not be filled.

    Built from the refusal's own headers, so it says which allowance ran out and when
    it comes back. Always returns a block: by the time this is called the request has
    already been refused, and a wall with no words is worse than a plain sentence.
    """
    found = _allowance(headers)
    scope = found[0] if found else "day"
    limit = found[1] if found else 0
    signed_in = _signed_in(headers)
    reset = _reset_phrase(scope, headers)

    if signed_in:
        title = "This month's searches are used up"
        block = {"detail": reset}
    elif scope == "day":
        title = "Today's free searches are used up"
        block = {"detail": _wall_detail(reset, "daily")}
    else:
        title = "The free searches are used up"
        block = {"detail": _wall_detail(reset, "monthly")}

    block.update({
        "state": "exhausted",
        "scope": scope,
        "limit": limit,
        "remaining": 0,
        "used": limit,
        "signed_in": signed_in,
        "title": title,
        "short": short(0, scope),
        "message": f"{title}. {block['detail']}",
        # The wall's own title, not the panel's (budgetWords in widget.py).
        "wall": True,
    })
    block.update(_link(signed_in))
    block.update(_wording_facts(scope, headers))
    return block


def _wall_detail(reset: str, which: str) -> str:
    if ACCOUNT_CTA:
        return f"{reset} A free Pexafy account lifts this {which} limit."
    return reset
