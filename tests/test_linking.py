"""Letting somebody attach an account — through the host's own flow.

A tool declaring both `noauth` and `oauth2`, plus a result carrying
`_meta["mcp/www_authenticate"]` with `isError`, makes ChatGPT offer to connect an
account (measured on preprod, 2026-09-14). That ends in a real bearer token, so the
person stops being an anonymous principal — and stops losing their budget when the
connector is reinstalled (`openai/subject` is an installation id). The offer is made
when somebody asks for it (connect_account); a spent allowance is drawn as a wall.

    pytest tests/test_linking.py -v
"""
from __future__ import annotations

import pytest

from pexafy_mcp import budget, linking


class TestChallenge:
    def test_it_points_at_the_protected_resource_metadata(self):
        got = linking.challenge("https://mcp.example.com/mcp", "Sign in to continue.")
        assert 'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"' in got
        assert got.startswith("Bearer ")

    def test_the_failure_is_scope_not_a_bad_token(self):
        """Nothing was wrong with the credential — there wasn't one, and the request
        was refused on allowance."""
        got = linking.challenge("https://mcp.example.com/mcp", "x")
        assert 'error="insufficient_scope"' in got
        assert "invalid_token" not in got

    def test_the_description_is_the_sentence_the_person_reads(self):
        got = linking.challenge("https://mcp.example.com/mcp", "Today's searches are used up.")
        assert 'error_description="Today\'s searches are used up."' in got

    def test_the_description_cannot_break_out_of_its_quotes(self):
        got = linking.challenge("https://mcp.example.com/mcp", 'Say "connect" \\ now\nplease')
        assert got.endswith('error_description="Say \\"connect\\" \\\\ now please"')

    def test_an_unknown_public_url_drops_the_metadata_rather_than_inventing_one(self):
        got = linking.challenge("", "x")
        assert "resource_metadata" not in got
        assert 'error="insufficient_scope"' in got


class TestSecuritySchemes:
    def test_noauth_comes_first_and_oauth2_is_offered(self):
        """A tool declaring only oauth2 would force a sign-in before the first search,
        and an app that REQUIRES an account is refused at directory review."""
        assert linking.SECURITY_SCHEMES[0] == {"type": "noauth"}
        assert {"type": "oauth2", "scopes": ["read"]} in linking.SECURITY_SCHEMES

    def test_the_scope_is_one_this_server_actually_issues(self):
        oauth = [s for s in linking.SECURITY_SCHEMES if s["type"] == "oauth2"][0]
        assert oauth["scopes"] == ["read"]


@pytest.mark.asyncio
async def test_every_tool_declares_optional_auth_on_the_wire():
    """`securitySchemes` is a field of the tool object, not of its `_meta`, and
    FastMCP's own Tool model forbids extras — so this can only be attached at the
    conversion to the protocol type. Which is exactly why it is worth pinning on the
    wire rather than on a Python object."""
    from fastmcp import Client

    from pexafy_mcp.server import build_server

    server = build_server()
    async with Client(server) as client:
        tools = await client.list_tools()

    assert tools, "no tools listed"
    for tool in tools:
        schemes = getattr(tool, "securitySchemes", None)
        # Some SDK versions surface unknown fields in model_extra instead.
        if schemes is None and getattr(tool, "model_extra", None):
            schemes = tool.model_extra.get("securitySchemes")
        assert schemes == linking.SECURITY_SCHEMES, f"{tool.name} declares {schemes!r}"


@pytest.mark.asyncio
async def test_the_schemes_are_mirrored_in_meta_beside_what_is_there():
    """OpenAI: `_meta["securitySchemes"]` is the "Back-compat mirror for clients that
    only read `_meta`". Merged into the tool's `_meta`, never in its place: the grid
    link and the status texts must survive it."""
    from fastmcp import Client
    from mcp.types import Implementation

    from pexafy_mcp import tooling
    from pexafy_mcp.server import build_server

    # Listed as ChatGPT, the host that reads the status texts (a client named "mcp", the
    # test client's default, reads the list without them: compat.DropToolStatusForStrictClients).
    async with Client(build_server(), client_info=Implementation(name="openai-mcp", version="1")) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for tool in tools.values():
        assert (tool.meta or {}).get("securitySchemes") == linking.SECURITY_SCHEMES, tool.name
    search = tools[tooling.PUBLIC_TOOL_NAMES["search_photos"]].meta
    assert "openai/toolInvocation/invoking" in search


def test_with_linking_off_neither_is_declared(monkeypatch):
    from fastmcp.tools.function_tool import FunctionTool

    def probe() -> str:
        return "x"

    linking.declare_optional_auth()
    tool = FunctionTool.from_function(probe, meta={"mine": 1})
    monkeypatch.setattr(linking, "ENABLED", False)
    wire = tool.to_mcp_tool()
    assert "securitySchemes" not in (wire.meta or {})
    assert (wire.meta or {}).get("mine") == 1
    monkeypatch.setattr(linking, "ENABLED", True)
    wire = tool.to_mcp_tool()
    assert wire.meta["securitySchemes"] == linking.SECURITY_SCHEMES
    assert wire.meta["mine"] == 1


@pytest.mark.asyncio
async def test_the_challenge_survives_the_whole_server_not_just_the_middleware():
    """The one that would have caught it.

    Calling the middleware directly proves nothing about integration: `FastMCP._call_tool`
    wraps every tool in `except Exception: raise ToolError(...)` INSIDE the middleware
    pipeline, so a plain Exception is reshaped before any middleware sees it. Preprod
    answered `isError: true` with `_meta: null` and offered nothing. This drives a real
    client against a real server.
    """
    from fastmcp import Client, FastMCP

    from pexafy_mcp import linking as lk

    server = FastMCP("wall-test")
    server.add_middleware(lk.OfferAccountLink("https://mcp.example.com/mcp"))

    @server.tool
    def search() -> str:
        raise lk.BudgetWall("Connect a Pexafy account.", asked=True)

    async with Client(server) as client:
        result = await client.call_tool("search", {}, raise_on_error=False)

    assert result.is_error is True
    meta = getattr(result, "meta", None) or {}
    assert lk.META_KEY in meta, f"no challenge in {meta!r}"
    assert meta[lk.META_KEY][0].startswith("Bearer ")
    assert "Connect a Pexafy account" in result.content[0].text
    # The host's own text, not FastMCP's "Error calling tool 'search': …" wrapper.
    assert "Error calling tool" not in result.content[0].text


@pytest.mark.asyncio
async def test_the_wall_comes_back_as_an_error_carrying_the_challenge():
    """The host requires `isError` before it will show the prompt, and reads the
    challenge from `_meta`. A ToolError could carry neither."""
    from fastmcp.server.middleware import MiddlewareContext

    mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

    async def call_next(_ctx):
        raise linking.BudgetWall("Connect a Pexafy account.", asked=True)

    result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)

    assert result.is_error is True
    assert "Connect a Pexafy account" in result.content[0].text
    challenge = result.meta[linking.META_KEY]
    assert isinstance(challenge, list) and len(challenge) == 1
    assert challenge[0].startswith("Bearer ")


@pytest.mark.asyncio
async def test_somebody_already_signed_in_is_offered_nothing():
    """Their allowance is their account's, and the only thing past it is a larger
    plan — the upgrade the platform's rules forbid us to promote."""
    from fastmcp.server.middleware import MiddlewareContext

    mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

    async def call_next(_ctx):
        raise linking.BudgetWall("This month's searches are used up.", signed_in=True)

    result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)

    assert result.is_error is True
    assert not result.meta or linking.META_KEY not in (result.meta or {})


@pytest.mark.asyncio
async def test_the_offer_can_be_switched_off_whole():
    from unittest import mock

    from fastmcp.server.middleware import MiddlewareContext

    mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

    async def call_next(_ctx):
        raise linking.BudgetWall("used up")

    with mock.patch.object(linking, "ENABLED", False):
        result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)
    assert result.is_error is True
    assert not result.meta


class TestErrorCode:
    """The host writes the words on its own dialog from this code, so it is a setting.

    On the first real run ChatGPT said "your connection has expired, reconnect" — about
    a credential that went stale, which is not what happened to somebody who never had
    one. The flow works either way (the account does get attached); the wording is the
    open question, and it must be retunable with a restart rather than a release.
    """

    def test_the_documented_code_is_the_default(self):
        assert linking.ERROR_CODE == "insufficient_scope"

    @pytest.mark.parametrize("setting", ["", "   "])
    def test_an_empty_setting_falls_back_to_the_default(self, reload_with_env, setting):
        """OpenAI: "make sure the value contains both an `error` and `error_description`
        parameter". An empty setting dropped `error` from the challenge."""
        reloaded = reload_with_env(linking, {"PEXAFY_LINK_ERROR": setting})
        assert reloaded.ERROR_CODE == "insufficient_scope"
        got = reloaded.challenge("https://mcp.example.com/mcp", "Sign in to continue.")
        assert 'error="insufficient_scope"' in got
        assert 'error_description="Sign in to continue."' in got
        assert "resource_metadata=" in got

    def test_another_code_is_sent_as_set(self, reload_with_env):
        reloaded = reload_with_env(linking, {"PEXAFY_LINK_ERROR": "invalid_token"})
        assert 'error="invalid_token"' in reloaded.challenge("https://mcp.example.com/mcp", "x")


class TestWhereTheOfferAppears:
    """Only where somebody asked for it.

    MEASURED on 2026-09-15 in a real ChatGPT session. Attaching the challenge to a
    spent allowance did two bad things: the host's dialog said "Your connection has
    expired. Reconnect it" on a connector that was never connected, and when the
    person pressed "Not now", the model did not receive the refusal at all — it told
    them it had just run the search that had been refused. A wall that ends in the
    assistant inventing results is worse than no wall.
    """

    @pytest.mark.asyncio
    async def test_running_out_does_not_summon_the_dialog(self):
        from fastmcp.server.middleware import MiddlewareContext

        mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

        async def call_next(_ctx):
            raise linking.BudgetWall("Today's free searches are used up.")

        result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)
        assert result.is_error is True
        assert not result.meta, "the wall must leave the refusal readable by the model"
        assert "used up" in result.content[0].text

    @pytest.mark.asyncio
    async def test_asking_does(self):
        from fastmcp.server.middleware import MiddlewareContext

        mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

        async def call_next(_ctx):
            raise linking.BudgetWall("Connect an account.", asked=True)

        result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)
        assert linking.META_KEY in (result.meta or {})

    @pytest.mark.asyncio
    async def test_the_wall_can_be_given_the_dialog_back(self):
        from unittest import mock

        from fastmcp.server.middleware import MiddlewareContext

        mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

        async def call_next(_ctx):
            raise linking.BudgetWall("used up")

        with mock.patch.object(linking, "LINK_AT_WALL", True):
            result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)
        assert linking.META_KEY in (result.meta or {})

    @pytest.mark.asyncio
    async def test_the_tool_that_exists_to_ask_asks(self):
        from pexafy_mcp import anonymous

        # Out of a request there is no identity, and no identity reads as "signed in" —
        # so the caller has to be put back in the one state where asking makes sense.
        token = anonymous.current.set(
            anonymous.Identity(subject="s", source="openai-subject",
                               client="chatgpt", attested=True))
        try:
            with pytest.raises(linking.BudgetWall) as caught:
                await linking.connect_account()
        finally:
            anonymous.current.reset(token)
        assert caught.value.asked is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("openai", "text"), [
        (True, linking.CONNECT_PROMPT),
        (False, linking.CONNECT_REQUEST_REPORT),
    ])
    async def test_the_words_follow_the_host_and_the_challenge_does_not(self, openai, text):
        """ChatGPT answers the challenge with its own dialog: its words are unchanged.
        Anywhere else nothing opens — Claude Code without an account passes the text to
        the model and moves on — so the text says what the result is and what follows,
        instead of asking for a connection. The challenge, and the `error_description`
        a host shows beside its button, are the same for every host."""
        from unittest import mock

        from fastmcp.server.middleware import MiddlewareContext

        from pexafy_mcp import anonymous

        async def call_next(_ctx):
            return await linking.connect_account()

        mw = linking.OfferAccountLink("https://mcp.example.com/mcp")
        token = anonymous.current.set(
            anonymous.Identity(subject="203.0.113.7", source="ip",
                               client="claude-code", attested=True))
        try:
            with mock.patch.object(linking.hosts, "is_openai", return_value=openai), \
                 mock.patch.object(linking, "ENABLED", True):
                result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)
        finally:
            anonymous.current.reset(token)
        assert result.is_error is True
        assert result.content[0].text == text
        challenge = result.meta[linking.META_KEY][0]
        assert f'error_description="{linking.CONNECT_PROMPT}"' in challenge

    @pytest.mark.asyncio
    async def test_the_probe_never_asks_for_anything(self, monkeypatch):
        """It runs once a second while the grid waits for a connection to complete;
        one that raised would summon the host's dialog over and over."""
        from pexafy_mcp import anonymous

        allowance = {"state": "warning", "remaining": 3}

        async def read_allowance():
            return allowance

        monkeypatch.setattr(linking, "read_allowance", read_allowance)
        token = anonymous.current.set(
            anonymous.Identity(subject="s", source="openai-subject",
                               client="chatgpt", attested=True))
        try:
            answer = await linking.connect_account(check_only=True)
        finally:
            anonymous.current.reset(token)
        assert answer.content[0].text == linking.NOT_CONNECTED
        # And it says what the allowance is now, which is the only way a grid showing
        # the wall can ever learn that the wall is gone.
        assert answer.structured_content == {"connected": False, "budget": allowance}

    @pytest.mark.asyncio
    async def test_a_probe_that_cannot_read_the_allowance_still_answers(self, monkeypatch, caplog):
        """A failed read costs the budget line, never the answer — and the log says why."""
        import httpx

        from pexafy_mcp import anonymous

        async def read_allowance():
            raise httpx.ConnectError("usage endpoint unreachable")

        monkeypatch.setattr(linking, "read_allowance", read_allowance)
        token = anonymous.current.set(
            anonymous.Identity(subject="s", source="openai-subject",
                               client="chatgpt", attested=True))
        try:
            with caplog.at_level("WARNING", logger="pexafy.mcp.linking"):
                answer = await linking.connect_account(check_only=True)
        finally:
            anonymous.current.reset(token)
        assert answer.structured_content == {"connected": False, "budget": None}
        assert "ConnectError: usage endpoint unreachable" in caplog.text

    @pytest.mark.asyncio
    async def test_the_probe_reads_the_allowance_from_the_usage_endpoint(self, fake_api):
        """Through the whole server: one GET /usage, which the quota exempts, and no search."""
        import httpx
        from fastmcp import Client

        from pexafy_mcp import server

        fake_api.respond = lambda request: httpx.Response(
            200, json={"success": True, "data": {}},
            headers={"X-Daily-Quota-Limit": "100", "X-Daily-Quota-Remaining": "5"})
        async with Client(server.build_server()) as client:
            answer = await client.call_tool(linking.CONNECT_TOOL, {"check_only": True})

        assert [(r.method, r.url.path) for r in fake_api.requests] == [("GET", "/api/v1/usage")]
        assert answer.structured_content["budget"]["remaining"] == 5
        # The model's half only; the panel's wording and its button reach the grid on
        # `_meta`, as on a search.
        assert set(answer.structured_content["budget"]) <= set(budget.MODEL_FIELDS)
        grid = answer.meta[budget.ACCOUNT_META_KEY]
        assert grid["budget"]["short"] == "5 searches left today"
        assert grid["budget"]["cta_label"] == "Sign in"


@pytest.mark.asyncio
async def test_a_refusal_carries_what_the_grid_needs_to_draw_the_wall():
    """A conversation that has already mounted the widget mounts it again for a
    refusal — measured 2026-09-15 — and with no structuredContent it sits on
    "Connected — waiting for results…" for good. The block costs nothing where the
    host drops it, and turns a hung frame into the screen with the button on it."""
    from fastmcp.server.middleware import MiddlewareContext

    mw = linking.OfferAccountLink("https://mcp.example.com/mcp")

    async def call_next(_ctx):
        raise linking.BudgetWall(
            "Today's free searches are used up.",
            block={"state": "exhausted", "title": "Today's free searches are used up",
                   "detail": "It resets at midnight UTC.", "cta_label": "Sign in",
                   "account_url": "https://pexafy.com/auth/login/"},
            account={"state": "anonymous", "label": "Sign in to Pexafy"})

    result = await mw.on_call_tool(MiddlewareContext(message=None), call_next)
    sc = result.structured_content
    assert sc["data"] == []
    assert sc["budget"]["state"] == "exhausted"
    assert "used up" in sc["notice"]
    # What only the grid draws is on `_meta`: who is asking, the wall's wording and its
    # button. The model's half keeps the numbers and the sentence.
    assert "account" not in sc
    for grid_only in ("title", "detail", "cta_label", "account_url"):
        assert grid_only not in sc["budget"], grid_only
    grid = result.meta[budget.ACCOUNT_META_KEY]
    assert grid["account"]["state"] == "anonymous"
    assert grid["budget"] == {"title": "Today's free searches are used up",
                              "detail": "It resets at midnight UTC.", "cta_label": "Sign in",
                              "account_url": "https://pexafy.com/auth/login/"}
