"""The metadata probe: what it reads, what it writes, and what it must never do.

Each test here pins a decision taken when the probe was written. The one that
matters most is the last: a probe that raises takes a search down with it, and
the whole point of the thing is to cost nothing.
"""
from __future__ import annotations

import logging

import pytest
from mcp.types import CallToolRequestParams, InitializeRequest

from pexafy_mcp import observe, tooling


class _Ctx:
    """The two fields the middleware reads off a MiddlewareContext."""

    def __init__(self, message, method):
        self.message = message
        self.method = method


async def _pass(context):
    return "answered"


def _call(meta: dict | None):
    payload = {"name": tooling.PUBLIC_TOOL_NAMES["search_photos"], "arguments": {"english_search_sentence": "a bench"}}
    if meta is not None:
        payload["_meta"] = meta
    return CallToolRequestParams.model_validate(payload)


def test_the_digest_is_the_one_the_django_ingester_uses():
    """Same sha1, same 20 hex. A different one makes the two datasets unjoinable."""
    import hashlib

    value = "user-abc"
    assert observe.digest(value) == hashlib.sha1(value.encode()).hexdigest()[:20]
    assert observe.digest("") == ""


def test_meta_falls_back_to_the_message_when_there_is_no_request_context():
    """A direct call — a unit test, a hook running outside a request — still reads."""
    params = _call({"openai/subject": "s1", "openai/organization": "org-7"})
    assert observe.meta_of(_Ctx(params, "tools/call")) == {
        "openai/subject": "s1",
        "openai/organization": "org-7",
    }


def test_initialize_meta_is_read_one_level_deeper():
    """`initialize` is the one method FastMCP hands over untouched, as a Request."""
    req = InitializeRequest.model_validate(
        {
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "openai-mcp", "version": "1.0.0"},
                "_meta": {"openai/organization": "org-7"},
            },
        }
    )
    ctx = _Ctx(req, "initialize")
    assert observe.meta_of(ctx) == {"openai/organization": "org-7"}
    assert observe.client_of(ctx) == "openai-mcp/1.0.0"


def test_progress_token_is_not_mistaken_for_a_host_field():
    """`progressToken` is declared by the model, so it never reaches model_extra."""
    params = _call({"progressToken": "p1"})
    assert observe.meta_of(_Ctx(params, "tools/call")) == {}


@pytest.mark.asyncio
async def test_a_value_is_never_logged_in_clear(caplog):
    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    params = _call({"openai/subject": "secret-person", "openai/locale": "fr-FR"})
    await observe.LogRequestMeta().on_message(_Ctx(params, "tools/call"), _pass)
    line = caplog.text
    assert "secret-person" not in line
    assert observe.digest("secret-person") in line
    # The key of a field we do not digest is still named — that is the discovery.
    assert "openai/locale" in line
    # …and its value is not.
    assert "fr-FR" not in line


@pytest.mark.asyncio
async def test_an_empty_meta_still_writes_a_line(caplog):
    """An absence is a measurement: a host that sends nothing must be countable."""
    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    await observe.LogRequestMeta().on_message(_Ctx(_call(None), "tools/list"), _pass)
    assert "keys=[]" in caplog.text


@pytest.mark.asyncio
async def test_a_broken_probe_never_costs_the_call(caplog):
    """The reason this middleware is allowed anywhere near production."""
    caplog.set_level(logging.WARNING, logger="pexafy.mcp.meta")

    class Exploding:
        @property
        def params(self):
            raise RuntimeError("boom")

    result = await observe.LogRequestMeta().on_message(_Ctx(Exploding(), "tools/call"), _pass)
    assert result == "answered"
    assert "meta probe failed" in caplog.text


@pytest.mark.asyncio
async def test_the_meta_a_real_client_sends_survives_the_whole_stack(caplog):
    """The test that would have caught the first version of this probe.

    FastMCP rebuilds the params object before middleware runs, so a probe reading
    `context.message` on a `tools/call` sees its own version meta and nothing else
    — which is exactly what the first production run logged, on 67 real calls. Here
    a client sends `_meta` through an actual server, and the line must carry it.
    """
    from fastmcp import FastMCP

    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    server = FastMCP("probe-test")
    server.add_middleware(observe.LogRequestMeta())

    @server.tool
    def echo(word: str) -> str:
        return word

    from fastmcp import Client

    async with Client(server) as client:
        await client.session.call_tool(
            "echo",
            {"word": "hi"},
            meta={"openai/subject": "person-1", "openai/organization": "org-7"},
        )

    calls = [r.getMessage() for r in caplog.records if "method=tools/call" in r.getMessage()]
    assert calls, "no tools/call line was logged at all"
    line = calls[-1]
    assert "openai/organization" in line
    assert f"subject={observe.digest('person-1')}" in line
    assert "org-7" not in line and "person-1" not in line
    # The workspace is named among the keys and nothing more: not even its digest.
    assert observe.digest("org-7") not in line and "org=" not in line


@pytest.mark.asyncio
async def test_only_the_subject_is_digested(caplog):
    """`openai/subject` identifies a caller for the allowance, as OpenAI documents it.
    The conversation and the workspace serve nothing this server does, so neither their
    value nor a digest of it is written — their names alone say the host sent them."""
    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    params = _call({"openai/subject": "person-1", "openai/session": "conv-9",
                    "openai/organization": "org-7"})
    await observe.LogRequestMeta().on_message(_Ctx(params, "tools/call"), _pass)
    line = caplog.text
    assert observe.IDENTIFYING == ("openai/subject",)
    assert f"subject={observe.digest('person-1')}" in line
    for value in ("conv-9", "org-7"):
        assert value not in line and observe.digest(value) not in line
    assert "session=" not in line and "org=" not in line
    assert "openai/session" in line and "openai/organization" in line


# ── Which client: the name it gives itself, on every `initialize` ────────────
# What decides the grid's `ui.domain` (hosts.py) is who the client says it is, and a
# directory's scan connects under a name documented nowhere: the line is where it is
# read. It was written only when the host sent `_meta`, which an `initialize` almost
# never carries.

def _initialize(name: str, version: str = "1.0.0") -> InitializeRequest:
    return InitializeRequest.model_validate({"method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": name, "version": version}}})


@pytest.mark.asyncio
async def test_an_initialize_names_its_client_without_any_meta(caplog):
    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    await observe.LogRequestMeta().on_message(_Ctx(_initialize("Anthropic/ClaudeAI"), "initialize"), _pass)
    assert "meta method=initialize keys=[] client=Anthropic/ClaudeAI/1.0.0 apps=no" in caplog.text


@pytest.mark.asyncio
async def test_a_real_handshake_logs_the_client_it_came_from(caplog):
    """Through an actual server: the `initialize` line carries the client's name."""
    from fastmcp import Client, FastMCP
    from mcp.types import Implementation

    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    server = FastMCP("probe-test")
    server.add_middleware(observe.LogRequestMeta())
    async with Client(server, client_info=Implementation(name="Anthropic-Directory", version="2")) as client:
        await client.list_tools()
    lines = [r.getMessage() for r in caplog.records if "method=initialize" in r.getMessage()]
    assert lines, "no initialize line was logged at all"
    assert lines[-1].endswith("client=Anthropic-Directory/2 apps=no"), lines[-1]


@pytest.mark.asyncio
async def test_a_client_that_names_itself_in_the_meta_is_logged(caplog):
    """Protocol 2026-07-28 opens no session: the name rides on each request's `_meta`."""
    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    key = "io.modelcontextprotocol/clientInfo"
    params = _call({key: {"name": "claude-ai", "version": "3"}})
    await observe.LogRequestMeta().on_message(_Ctx(params, "tools/call"), _pass)
    assert f"keys=['{key}'] client=claude-ai/3" in caplog.text


@pytest.mark.asyncio
async def test_a_client_name_is_one_short_line_and_nothing_else(caplog):
    """The name is the client's to choose: it cannot open a second line, nor run on."""
    caplog.set_level(logging.INFO, logger="pexafy.mcp.meta")
    forged = "x\nmeta method=tools/call subject=forged\r" + "y" * 500
    await observe.LogRequestMeta().on_message(_Ctx(_initialize(forged), "initialize"), _pass)
    (line,) = [r.getMessage() for r in caplog.records]
    assert "\n" not in line and "\r" not in line
    assert len(line.split(" client=", 1)[1].rsplit(" apps=", 1)[0]) <= 80
