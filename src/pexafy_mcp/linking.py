"""Letting somebody who has no account attach one — through the host, not around it.

OpenAI's Apps SDK supports "anonymous first, link later": a tool that declares BOTH
`noauth` and `oauth2` may be used without an account, and a tool result carrying
`_meta["mcp/www_authenticate"]` with `isError` makes the host itself offer to connect
one — its own button, its own OAuth flow, ending in a real bearer token. The person
then searches as a Pexafy account, with that account's allowance, and keeps it when the
connector is reinstalled; an `openai/subject` does not survive that (it is an
installation id).

This module holds that flow:

  * `declare_optional_auth` — the `securitySchemes` every tool declares;
  * `connect_account` — the tool somebody reaches by asking to connect, or through the
    grid's account button. It raises `BudgetWall`, which is what makes the host show
    its dialog; with `check_only` it is a probe that never raises;
  * `OfferAccountLink` — turns a `BudgetWall` into the error result carrying the
    challenge.

A spent allowance does not summon the dialog by default. It comes back as an ordinary
empty answer the grid draws as a wall (server._wall_as_grid): the dialog would replace
that answer, and the model would never learn of the refusal. See LINK_AT_WALL.

The OAuth server, DCR and the protected-resource metadata live elsewhere (Django, auth.py).

**On wording.** The platform's rules forbid promoting upgrades and selling
subscriptions, and permit explaining why something is unavailable and signing in to an
account. The sentences here state the limit, say a free account lifts it, and stop.

    PEXAFY_ACCOUNT_LINKING  1                   declare optional auth, register connect_account
    PEXAFY_LINK_AT_WALL     0                   offer the dialog at a spent allowance too
    PEXAFY_LINK_ERROR       insufficient_scope  the `error` code of the challenge
"""
from __future__ import annotations

import logging
import os

from fastmcp.exceptions import FastMCPError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from . import anonymous, budget, hosts
from .env import flag

logger = logging.getLogger("pexafy.mcp.linking")

META_KEY = "mcp/www_authenticate"

ENABLED = flag("PEXAFY_ACCOUNT_LINKING", True)

# The `error` code in the challenge — a setting, because the host writes the words of
# its dialog from it, not from our `error_description`. With `insufficient_scope`, the value the Apps SDK
# documents, the flow works (the account does get attached), but ChatGPT's dialog
# speaks of an expired connection to somebody who never had one. Whether another value
# reads closer to the truth only a real host can tell, so it is retuned with a restart.
#
# Never none: OpenAI asks that the challenge carry "both an `error` and
# `error_description` parameter", so an empty setting falls back to the default rather
# than dropping the code.
#
#   PEXAFY_LINK_ERROR=insufficient_scope   (default, documented by the Apps SDK)
DEFAULT_ERROR_CODE = "insufficient_scope"
ERROR_CODE = os.environ.get("PEXAFY_LINK_ERROR", "").strip() or DEFAULT_ERROR_CODE

# Whether a BudgetWall raised by a spent allowance carries the challenge too. That only
# happens with PEXAFY_BUDGET_WALL_GRID=0: by default a spent allowance is an empty
# answer, not a BudgetWall (server._wall_as_grid). Off by default, for what a real
# ChatGPT session showed on 2026-09-15:
#
#   * the dialog is ChatGPT's own and ignores our `error_description` — it reads
#     "Your connection has expired. Reconnect it" on a connector that was never
#     connected;
#   * when the person presses "Not now", the model does not receive the refusal: it
#     told them it had just run the search that had been refused. A wall that ends in
#     the assistant inventing results is worse than no wall.
#
# So the dialog is offered where somebody ASKS for it: the account button, or "connect
# my Pexafy account", both of which reach `connect_account`.
LINK_AT_WALL = flag("PEXAFY_LINK_AT_WALL", False)

# Both, in this order. `noauth` first says the tool works with no account at all —
# which is true, and is what keeps the connector usable the moment it is installed;
# `oauth2` says an account may be attached. A tool that declared only oauth2 would
# force a sign-in before the first search, and an app that REQUIRES an account is
# refused at directory review.
#
# `read` is the scope this server actually issues (see the protected-resource
# metadata); naming one it does not issue would fail the exchange.
SECURITY_SCHEMES = [
    {"type": "noauth"},
    {"type": "oauth2", "scopes": ["read"]},
]


def resource_metadata_url(public_url: str) -> str:
    """Where the host reads what it needs to run the OAuth flow (RFC 9728)."""
    base = (public_url or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return f"{base}/.well-known/oauth-protected-resource"


def challenge(public_url: str, description: str) -> str:
    """The WWW-Authenticate value the host turns into a "connect account" button.

    `insufficient_scope` rather than `invalid_token`: nothing was wrong with a
    credential — there was none. Which code to send is a setting, because the host
    writes its own words from it; see ERROR_CODE. There is always one.
    """
    metadata = resource_metadata_url(public_url)
    parts = []
    if metadata:
        parts.append(f'resource_metadata="{metadata}"')
    parts.append(f'error="{ERROR_CODE or DEFAULT_ERROR_CODE}"')
    parts.append(f'error_description="{_quoted(description)}"')
    return "Bearer " + ", ".join(parts)


def _quoted(text: str) -> str:
    """`text` as the inside of an RFC 9110 quoted-string: `\\` and `"` escaped, and
    control characters (a line break included), which it cannot hold, turned into spaces."""
    text = "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in text)
    return text.replace("\\", "\\\\").replace('"', '\\"')


class BudgetWall(FastMCPError):
    """A refusal that has to reach the host as a RESULT: raised by `connect_account`,
    and by a spent allowance when the wall is not drawn as a grid; answered by
    `OfferAccountLink` at the tool-call boundary.

    Not a ToolError, because a ToolError is a message and this has to become a result
    with `_meta` on it, which only the tool-call boundary can build. But it IS a
    FastMCPError, and that is not decoration: `FastMCP._call_tool` wraps the tool in
    `except Exception: raise ToolError(f"Error calling tool {name!r}: {e}")`, INSIDE the
    middleware pipeline, so a plain Exception is reshaped before any middleware sees it
    (on preprod: `isError: true`, `_meta: null`, no offer). FastMCPError is the one
    branch of that handler that re-raises the exception it was given.

    `log_level` is INFO: somebody reaching the end of their allowance is the feature
    working, not an error, and it should not page anyone.
    """

    def __init__(self, message: str, *, prompt: str = "", signed_in: bool = False,
                 asked: bool = False, block: dict | None = None,
                 account: dict | None = None):
        super().__init__(message, log_level=logging.INFO)
        # Two texts, because they are read by two different readers. `message` is for
        # the assistant, a report in the third person ("Pexafy refused this search: …").
        # `prompt` is what the HOST puts next to its connect button, and a dialog that
        # opens with a report addressed to the assistant is the assistant talking to
        # itself in front of a person. Written for them instead.
        self.message = message
        self.prompt = prompt or message
        self.signed_in = signed_in
        # Whether somebody ASKED to connect, as opposed to simply running out. The
        # difference decides whether the host's dialog appears: asked for, it is the
        # answer; unasked, it hides the refusal behind a sentence about an expired
        # connection that never existed.
        self.asked = asked
        # What the grid needs to draw the wall. A conversation that has already shown
        # the grid mounts it again for a refusal, and with no structuredContent the
        # frame waits for results forever. ChatGPT does not pass an error result's
        # structuredContent to the frame (measured 2026-09-15); a host that does gets
        # the wall, and one that does not loses nothing.
        self.block = block
        self.account = account


# ── The tool that asks ───────────────────────────────────────────────────────
# Attaching an account should not require running out first. The widget SDK has no
# method for authentication (`ui/open-link`, `ui/message`, `ui/update-model-context`…
# and nothing else), and the sign-in page creates an account without attaching it to
# the connector. A tool call refused with the challenge is the only thing that makes a
# host offer its flow — so the request becomes a tool call, and the grid's account
# button makes it too.
CONNECT_TOOL = "connect_account"

CONNECT_TITLE = "Connect a Pexafy account"

# What the tool does, stated as it happens — including in a host that ignores the
# challenge — rather than as orders about how to report it. The mechanism is unchanged:
# the refusal still carries the challenge (OfferAccountLink), and "the error is how that
# request is delivered, not a failure" says as a fact what an order used to impose.
#
# The grid's account button, and the probe the grid sets `check_only` for, are named
# only where there is a grid (previews configured): without one, `check_only` is the
# model's alone, for a question about the state.
def connect_description(*, grid: bool = True) -> str:
    """The tool description, with the grid or without it."""
    button = " — including from the account button in the Pexafy grid —" if grid else ","
    probe = ("`check_only` is set by the Pexafy grid to read the state without opening a prompt"
             if grid else "`check_only` reads the state without opening a prompt")
    return (
        f"Asks the host to offer its own flow for connecting a Pexafy account, when none is attached. Use it when the person asks to connect, link or sign in to a Pexafy account{button} or wants more searches than the daily allowance available without an account.\n"
        "\n"
        "When no account is attached, it returns an error result that carries an account-connection request for the host: the error is how that request is delivered, not a failure. A host that supports it shows its own account-connection prompt, which the person completes or dismisses; other hosts show only the result's text. Calling it again returns the same request. When an account is already attached, it says so and starts nothing.\n"
        "\n"
        f"It does not search, and searching does not require calling it. {probe}; leave it unset when the person wants to connect or sign in."
    )


# The full text, as production serves it (with the grid).
CONNECT_DESCRIPTION = connect_description()

# The challenge's `error_description`, which the host may show beside its button, and
# the result's text for an OpenAI host, whose dialog is the answer to the request.
CONNECT_PROMPT = (
    "Connect a Pexafy account to search with that account's allowance instead of the "
    "daily one."
)

# The result's text anywhere else. A host that does not act on the challenge shows
# nothing but this text (Claude Code without an account: "Claude passes the error text
# to the model as the tool result and moves on, and there is no auth prompt"), so it
# says what the result is and what follows, rather than asking for a connection that
# host cannot start.
CONNECT_REQUEST_REPORT = (
    "No Pexafy account is attached to this connection. This result asks the host to "
    "offer its own account-connection prompt; a host that does not support that request "
    "shows nothing more, and searches then continue on the daily allowance available "
    "without an account."
)

ALREADY_CONNECTED = (
    "This conversation already searches with a Pexafy account; searches count against "
    "that account's allowance. There is nothing to connect."
)


NOT_CONNECTED = (
    "This conversation is searching without an account, on the daily allowance."
)

# How the probe reads the CURRENT allowance. Set by build_server, because asking the
# API from here would import the HTTP client that imports this module. None until then
# (a unit test calling the tool directly): the probe answers about the account only.
#
# A grid showing the wall has no other way to learn that the wall is gone. An allowance
# comes back — midnight, an account attached, a plan changed — and the frame only ever
# hears from a tool result it can no longer produce: without this, it stayed on "This
# month's searches are used up" after a real upgrade.
read_allowance = None  # async () -> dict | None


async def connect_account(check_only: bool = False):
    """Ask the host to offer the account-connection flow.

    Raising is the point: a returned result is a SUCCESS, and a host shows no prompt
    for a success. `OfferAccountLink` turns the raise into a refusal carrying `_meta`,
    which is what the host reads. A caller who is already connected gets an answer
    instead, since there is nothing to offer.

    `check_only` is for the grid. The connection completes inside the host and nothing
    tells the widget it happened, so the grid polls this probe: it answers "connected"
    or "not connected" WITHOUT ever raising, with the current allowance. It costs no
    search — its one call goes to the usage endpoint, which the quota exempts.
    """
    connected = anonymous.current.get() is None
    if check_only:
        # A probe, not a request: it never raises, so the host never shows a dialog for
        # it, and it never reaches a search — `read_allowance` asks the usage endpoint,
        # which is exempt from the quota it reports on.
        answer = ALREADY_CONNECTED if connected else NOT_CONNECTED
        block = None
        if read_allowance is not None:
            try:
                block = await read_allowance()
            except Exception as exc:  # noqa: BLE001 — a probe must not fail the frame
                logger.warning("Could not read the current allowance: %s: %s",
                               type(exc).__name__, exc)
        # The numbers and the sentence only: the panel's wording goes to the grid on
        # `_meta`, like every answer's (budget.for_model; origin.AttachOrigin).
        return ToolResult(
            content=[TextContent(type="text", text=answer)],
            structured_content={"connected": connected, "budget": budget.for_model(block)},
        )
    # Somebody arriving here with a credential is already through the door; sending
    # them round it again would be a dead end.
    if connected:
        # A ToolResult, not the bare text: the tool declares an output schema, and
        # FastMCP refuses a result whose structured content is not a dict.
        return ToolResult(
            content=[TextContent(type="text", text=ALREADY_CONNECTED)],
            structured_content={"connected": True, "budget": None},
        )
    # The challenge is the same everywhere; only the words the model reads differ, by
    # what the host does with the challenge (hosts.is_openai: coarse detection, which
    # authorizes nothing here).
    message = CONNECT_PROMPT if hosts.is_openai() else CONNECT_REQUEST_REPORT
    raise BudgetWall(message, prompt=CONNECT_PROMPT, asked=True)


def declare_optional_auth() -> bool:
    """Put `securitySchemes` on every tool, at the one point where it can go.

    `securitySchemes` is a field of the TOOL OBJECT, not of its `_meta` — and there is
    no seam for it. FastMCP's own `Tool` model sets `extra="forbid"`, so it cannot be
    attached when a tool is defined; a middleware's `on_list_tools`/`on_message` hands
    back those same FastMCP tools, so it cannot be attached there either (measured: the
    result of `tools/list` inside a middleware is a list of `TransformedTool`, and the
    conversion happens after every middleware has run). The protocol type,
    `mcp.types.Tool`, does allow extras and does serialise them.

    That leaves the conversion itself, `Tool.to_mcp_tool`, which is wrapped here once.
    It is a library method and this is a patch of one — the honest name for it — so the
    test that pins it (`test_every_tool_declares_optional_auth_on_the_wire`) asserts on
    what comes back over a real client, not on this function: if a FastMCP upgrade moves
    the seam, the test fails on the wire rather than silently declaring nothing.

    The host reads this at `tools/list` and nowhere else, which is why a connector has
    to be REMOVED and ADDED AGAIN for the change to be seen.

    The same list goes into the tool's `_meta` as well, merged with what is there: OpenAI
    documents `_meta["securitySchemes"]` as the "Back-compat mirror for clients that only
    read `_meta`", and its own example declares both.
    """
    from fastmcp.tools.tool import Tool

    if getattr(Tool.to_mcp_tool, "_pexafy_optional_auth", False):
        return False
    original = Tool.to_mcp_tool

    def to_mcp_tool(self, **overrides):
        mcp_tool = original(self, **overrides)
        if ENABLED:
            try:
                mcp_tool.securitySchemes = SECURITY_SCHEMES
                mcp_tool.meta = {**(mcp_tool.meta or {}), "securitySchemes": SECURITY_SCHEMES}
            except (AttributeError, ValueError):  # pragma: no cover — model changed
                logger.warning("Could not declare securitySchemes on %r",
                               getattr(mcp_tool, "name", "?"))
        return mcp_tool

    to_mcp_tool._pexafy_optional_auth = True
    Tool.to_mcp_tool = to_mcp_tool
    logger.info("Optional authentication declared on every tool (noauth + oauth2)")
    return True


class OfferAccountLink(Middleware):
    """Answer a BudgetWall with an error result, carrying the host's offer to connect.

    `isError`, because the host requires it before it will show its prompt; the
    challenge in `_meta`, when somebody asked to connect (or LINK_AT_WALL is on) and is
    not signed in. The text is what a host without that support shows instead, so it
    has to stand on its own.
    """

    def __init__(self, public_url: str = ""):
        self.public_url = public_url

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except BudgetWall as wall:
            meta = None
            # Nothing to offer somebody already signed in: their allowance is their
            # account's, and the only thing past it is a larger plan — which is the
            # upgrade the platform's rules forbid us to promote. And nothing at a wall
            # the person did not ask to be at — see LINK_AT_WALL.
            if ENABLED and not wall.signed_in and (wall.asked or LINK_AT_WALL):
                meta = {META_KEY: [challenge(self.public_url, wall.prompt)]}
                logger.info("Offered account linking (%s)",
                            "asked" if wall.asked else "at the wall")
            structured = None
            if wall.block:
                structured = {"success": False, "data": [], "notice": wall.message,
                              "budget": budget.for_model(wall.block)}
            # What only the grid draws — who is asking, the wall's wording and its
            # button — on `_meta`, like every answer's (budget.ACCOUNT_META_KEY).
            grid = {}
            if wall.account:
                grid["account"] = wall.account
            half = budget.for_grid(wall.block)
            if half:
                grid["budget"] = half
            if grid:
                meta = {**(meta or {}), budget.ACCOUNT_META_KEY: grid}
            return ToolResult(
                content=[TextContent(type="text", text=wall.message)],
                structured_content=structured,
                meta=meta,
                is_error=True,
            )
