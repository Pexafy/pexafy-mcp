"""The metadata ChatGPT reads on the inline-grid MCP App resource.

Two facts travel twice: FastMCP writes the spec form under `_meta.ui`, and
ChatGPT reads its own compatibility aliases, `openai/widgetDomain` and
`openai/widgetCSP`. Both are written from the same values in build_server, and
these tests exist to keep them from drifting apart — a drift nothing else would
catch, because each side is invisible to the other's reader.

The domain matters at submission: without it OpenAI's portal refuses the app
("a unique domain is required for app validation"). The CSP alias matters after
it: it decides whether thumbnails load once the app is published rather than
previewed, which cannot be tested before publishing.
"""
from __future__ import annotations

import pytest

from pexafy_mcp import previews, server


@pytest.fixture
def previews_on(reload_with_env):
    """The grid is only registered when the thumbnail CDN is configured."""
    reload_with_env(previews, {
        "PEXAFY_THUMB_BASE_URL": "https://thumb.pexafy.com",
        "PEXAFY_THUMB_HMAC_SECRET": "test-secret",
    })


async def _grid_meta():
    """The grid's `_meta` as registered — the value a Claude host is served. What other
    hosts receive in its place is tested under "by host" below."""
    for resource in await server.build_server().list_resources(run_middleware=False):
        if str(resource.uri) == server.widget.GRID_URI:
            return resource.to_mcp_resource().meta or {}
    raise AssertionError("the grid resource is not registered")


async def test_the_two_domain_fields_are_different_things(previews_on):
    """They were declared as the same value, and that stopped Claude rendering at all:

        Invalid ui.domain format: expected "{hash}.claudemcpcontent.com",
        got "https://preprod.pexafy.com"

    `openai/widgetDomain` is our own origin, under the name ChatGPT reads.
    `_meta.ui.domain` is the spec's field, and the spec's format is a sha256 of this
    server's endpoint under claudemcpcontent.com. Asserting they match is what let the
    confusion ship."""
    meta = await _grid_meta()
    assert meta["openai/widgetDomain"] == server.WIDGET_DOMAIN
    assert meta["ui"]["domain"] != server.WIDGET_DOMAIN
    assert meta["ui"]["domain"].endswith(".claudemcpcontent.com")


async def test_the_spec_domain_is_derived_from_this_server_endpoint(previews_on):
    """Derived, not issued: no allow-list, no credential — and recomputing it is the
    only way to know it is right."""
    import hashlib

    base = server.MCP_PUBLIC_URL.rstrip("/")
    endpoint = base if base.endswith("/mcp") else f"{base}/mcp"
    expected = hashlib.sha256(endpoint.encode()).hexdigest()[:32] + ".claudemcpcontent.com"
    assert (await _grid_meta())["ui"]["domain"] == expected


async def test_the_endpoint_hashed_carries_the_mcp_path(previews_on):
    """The string has to be exact — scheme, host, /mcp, no trailing slash. A hash of
    the bare host is a valid-looking domain that Claude still refuses."""
    import hashlib

    bare = hashlib.sha256(server.MCP_PUBLIC_URL.rstrip("/").encode()).hexdigest()[:32]
    assert not (await _grid_meta())["ui"]["domain"].startswith(bare)


async def test_the_widget_domain_is_an_https_origin_with_no_path(previews_on):
    """It is an origin, not a URL: a trailing path fails validation."""
    domain = (await _grid_meta())["openai/widgetDomain"]
    assert domain.startswith("https://")
    assert domain.count("/") == 2, domain


async def test_the_two_csp_encodings_carry_the_same_domains(previews_on):
    """The spec form is camelCase, the alias snake_case; the values must match."""
    meta = await _grid_meta()
    spec, alias = meta["ui"]["csp"], meta["openai/widgetCSP"]
    assert alias["resource_domains"] == spec.get("resourceDomains", [])
    assert alias["connect_domains"] == spec.get("connectDomains", [])


async def test_the_thumbnail_origin_is_allowed_to_load(previews_on):
    """Whatever else changes, the grid must be able to fetch its thumbnails."""
    meta = await _grid_meta()
    assert previews.THUMB_ORIGIN in meta["openai/widgetCSP"]["resource_domains"]


def test_the_default_domain_is_not_openais_shared_sandbox():
    """The shared default is precisely what the portal refuses."""
    assert "oaiusercontent.com" not in server.WIDGET_DOMAIN
    assert server.WIDGET_DOMAIN.startswith("https://")


# ── `_meta.ui.domain`, by host ────────────────────────────────────────────────
# The spec leaves the field's format to each host. Claude wants its hash and renders
# nothing without it; OpenAI reads the same field as the app's own origin ("Dedicated
# origin for hosted components … must be unique per plugin"), `openai/widgetDomain`
# being only its alias. Claude can be recognised — by its name, in its session or in
# the request's `_meta`, or by the network it calls from — so a Claude host is served
# the hash, and every other one — OpenAI, or a client that cannot be told, such as a
# review scanner whose name is not documented — the website.

async def _grid_meta_seen_by(client_name: str | None) -> tuple[dict, dict]:
    """The grid's `_meta` in `resources/list` and in `resources/read`, as a client
    with that `clientInfo.name` receives them."""
    from fastmcp import Client
    from mcp.types import Implementation

    info = Implementation(name=client_name, version="1") if client_name else None
    async with Client(server.build_server(), client_info=info) as client:
        listed = next(r for r in await client.list_resources()
                      if str(r.uri) == server.widget.GRID_URI)
        read = await client.read_resource(server.widget.GRID_URI)
    return listed.meta or {}, read[0].meta or {}


async def test_an_openai_host_is_served_the_website_as_ui_domain(previews_on):
    listed, read = await _grid_meta_seen_by("openai-mcp")
    for meta in (listed, read):
        assert meta["ui"]["domain"] == server.WIDGET_DOMAIN
        assert meta["openai/widgetDomain"] == server.WIDGET_DOMAIN
        # Only the domain differs: the CSP is the same one every host gets.
        assert meta["ui"]["csp"]["connectDomains"] == meta["openai/widgetCSP"]["connect_domains"]


async def test_claude_hosts_keep_the_claude_domain(previews_on):
    claude = server._spec_ui_domain()
    for name in ("Anthropic/ClaudeAI", "claude-ai", "Anthropic", "claude-code"):
        listed, read = await _grid_meta_seen_by(name)
        assert listed["ui"]["domain"] == claude, name
        assert read["ui"]["domain"] == claude, name


async def test_a_host_that_cannot_be_told_is_served_the_website(previews_on):
    """OpenAI's review scans the server with a client whose name is not documented: if it
    is neither `openai-mcp` nor sends `openai/…` keys, it must still read the website as
    the app's origin, not Claude's hash."""
    for name in ("some-inspector", "OpenAI Apps Scanner", "mcp-scan", None):
        listed, read = await _grid_meta_seen_by(name)
        assert listed["ui"]["domain"] == server.WIDGET_DOMAIN, name
        assert read["ui"]["domain"] == server.WIDGET_DOMAIN, name


async def test_serving_openai_leaves_the_registered_value_alone(previews_on):
    """The rewrite is a copy: the next host must not inherit the last one's domain."""
    await _grid_meta_seen_by("openai-mcp")
    listed, read = await _grid_meta_seen_by("claude-ai")
    assert listed["ui"]["domain"] == read["ui"]["domain"] == server._spec_ui_domain()


def test_the_host_is_told_by_its_name_or_by_its_meta():
    from pexafy_mcp import hosts

    class _Session:
        def __init__(self, name):
            self.client_params = type("P", (), {"clientInfo": type("I", (), {"name": name})()})()

    class _Request:
        def __init__(self, keys):
            self.meta = type("M", (), {"model_extra": {k: "x" for k in keys}})()

    class _Ctx:
        def __init__(self, name, keys=()):
            self.fastmcp_context = type("C", (), {"session": _Session(name),
                                                  "request_context": _Request(keys)})()

    assert hosts.is_openai(_Ctx("openai-mcp"))
    assert hosts.is_openai(_Ctx("ChatGPT"))
    assert hosts.is_openai(_Ctx("", keys=["openai/subject"]))
    assert not hosts.is_openai(_Ctx("Anthropic/ClaudeAI"))
    assert not hosts.is_openai(_Ctx("claude-code", keys=["claudecode/toolUseId"]))
    assert not hosts.is_openai(None)            # no request at all: the earlier behaviour

    # Claude by its name alone, whatever the case; nothing else is Claude.
    for name in ("Anthropic/ClaudeAI", "claude-ai", "Anthropic", "claude-code"):
        assert hosts.is_claude(_Ctx(name)), name
    for name in ("openai-mcp", "ChatGPT", "some-inspector", ""):
        assert not hosts.is_claude(_Ctx(name)), name
    assert not hosts.is_claude(None)


# A request's `_meta`, as the SDK hands it over: the client's own keys in `model_extra`.
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"


class _Unnamed:
    """A context whose session holds no `clientInfo` — a stateless transport, a session
    that never said who it was — and whose request carries `meta`."""

    def __init__(self, meta: dict | None = None):
        session = type("S", (), {"client_params": None})()
        request = type("R", (), {"meta": type("M", (), {"model_extra": dict(meta or {})})()})()
        self.fastmcp_context = type("C", (), {"session": session, "request_context": request})()


def test_a_claude_host_that_opens_no_session_is_told_by_the_meta_it_sends():
    """Protocol revision 2026-07-28 opens no session: `clientInfo` travels in the `_meta`
    of every request. A Claude client there, served the website, would refuse the grid."""
    from pexafy_mcp import hosts

    for name in ("claude-ai", "Anthropic/ClaudeAI", "claude-code"):
        context = _Unnamed({CLIENT_INFO_KEY: {"name": name, "version": "1"}})
        assert hosts.is_claude(context), name
        assert hosts.client_name(context) == name
    # The same key names an OpenAI client just as well, and nothing else is Claude.
    openai = _Unnamed({CLIENT_INFO_KEY: {"name": "openai-mcp", "version": "1"}})
    assert hosts.is_openai(openai) and not hosts.is_claude(openai)
    for meta in ({}, {CLIENT_INFO_KEY: {"name": "some-inspector"}}, {CLIENT_INFO_KEY: "claude"},
                 {CLIENT_INFO_KEY: {"version": "1"}}):
        assert not hosts.is_claude(_Unnamed(meta)), meta


@pytest.mark.parametrize(("headers", "claude"), [
    ({"cf-connecting-ip": "160.79.104.20"}, True),
    ({"cf-connecting-ip": "160.79.111.254"}, True),            # the /21's far end
    ({"x-forwarded-for": "160.79.105.3, 10.0.0.2"}, True),     # leftmost: the caller
    ({"x-real-ip": "::ffff:160.79.106.1"}, True),              # IPv4 written as IPv6
    ({"cf-connecting-ip": "160.79.112.1"}, False),             # just outside
    ({"cf-connecting-ip": "203.0.113.5"}, False),
    ({"cf-connecting-ip": "not-an-address"}, False),
    ({}, False),                                               # stdio, a unit call
])
def test_a_claude_host_that_gives_no_name_is_told_by_the_network_it_calls_from(
        monkeypatch, headers, claude):
    """Every request from Anthropic's servers leaves 160.79.104.0/21, and OpenAI's never
    does: a Claude host with no name, or one no marker matches, is still Claude."""
    from pexafy_mcp import anonymous, hosts

    monkeypatch.setattr(anonymous, "_incoming_headers", lambda: dict(headers))
    for context in (_Unnamed(), _Unnamed({CLIENT_INFO_KEY: {"name": "some-inspector"}})):
        assert hosts.is_claude(context) is claude, headers
        # The network says Claude, never OpenAI: ChatGPT's answers stay ChatGPT's.
        assert not hosts.is_openai(context)


# The server over HTTP, as Claude reaches it: the caller's address in the header the
# CDN in front of it sets, and nothing else to go by.
_ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, body: dict, headers: dict, session: str | None = None) -> tuple[dict, str]:
    """POST one JSON-RPC message; (its answer, the session id the server gave)."""
    import json

    extra = {"mcp-session-id": session} if session else {}
    response = client.post("/mcp", content=json.dumps(body), headers={**_ACCEPT, **headers, **extra})
    assert response.status_code in (200, 202), response.text
    answer = {}
    if response.text.strip():
        lines = [line[5:] for line in response.text.splitlines() if line.startswith("data:")]
        answer = json.loads(lines[-1] if lines else response.text)
    return answer, response.headers.get("mcp-session-id", session or "")


def _domains_over_http(app, headers: dict, *, name: str | None = "remote-connector",
                       meta: dict | None = None) -> tuple[str, str]:
    """`ui.domain` in `resources/list` and in `resources/read`, over the HTTP transport:
    after an `initialize` naming `name`, or, with `name=None`, with no handshake at all
    (a stateless server), `meta` riding on each request."""
    from starlette.testclient import TestClient

    params = {"_meta": meta} if meta else {}
    with TestClient(app) as client:
        session = None
        if name is not None:
            _, session = _rpc(client, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": name, "version": "1"}}}, headers)
            _rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, session)
        listed, _ = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "resources/list",
                                  "params": params}, headers, session)
        read, _ = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                                "params": {"uri": server.widget.GRID_URI, **params}}, headers, session)
    grid = next(r for r in listed["result"]["resources"] if r["uri"] == server.widget.GRID_URI)
    return grid["_meta"]["ui"]["domain"], read["result"]["contents"][0]["_meta"]["ui"]["domain"]


def test_over_http_claude_s_network_is_served_claude_s_domain(previews_on):
    """A client whose name says nothing, calling from Anthropic's network: the hash. The
    same client from anywhere else: the website, as before."""
    app = server.http_app(server.build_server())
    claude = server._spec_ui_domain()
    assert _domains_over_http(app, {"CF-Connecting-IP": "160.79.104.20"}) == (claude, claude)
    assert _domains_over_http(app, {"CF-Connecting-IP": "203.0.113.5"}) == (
        server.WIDGET_DOMAIN, server.WIDGET_DOMAIN)


def test_over_a_stateless_transport_claude_is_told_by_its_meta_or_its_network(previews_on):
    """No session, so no `clientInfo` from an `initialize` — the case of
    FASTMCP_STATELESS_HTTP, and of a 2026-07-28 client, which names itself in `_meta`."""
    app = server.build_server().http_app(stateless_http=True)
    claude = server._spec_ui_domain()
    named = {CLIENT_INFO_KEY: {"name": "claude-ai", "version": "1"}}
    elsewhere = {"CF-Connecting-IP": "203.0.113.5"}
    assert _domains_over_http(app, elsewhere, name=None, meta=named) == (claude, claude)
    assert _domains_over_http(app, {"CF-Connecting-IP": "160.79.104.20"}, name=None) == (
        claude, claude)
    assert _domains_over_http(app, elsewhere, name=None) == (
        server.WIDGET_DOMAIN, server.WIDGET_DOMAIN)


# ── The CSP: what the grid actually reaches ──────────────────────────────────

async def test_the_thumbnails_are_images_not_a_connection(previews_on):
    """The grid draws its thumbnails and never reads their bytes (`uploadFile` is gone):
    the CDN is a resource domain only. `fetch` goes to this server's /selection alone.
    "The plugin review process checks the declared policy against the UI behavior."""
    meta = await _grid_meta()
    connect = meta["ui"]["csp"]["connectDomains"]
    assert previews.THUMB_ORIGIN not in connect
    assert previews.THUMB_ORIGIN not in meta["openai/widgetCSP"]["connect_domains"]
    assert connect == [server.MCP_PUBLIC_URL.rstrip("/")]
    assert previews.THUMB_ORIGIN in meta["ui"]["csp"]["resourceDomains"]


def test_the_widget_fetches_this_server_and_nothing_else():
    """The CSP above is only honest while this holds: every `fetch` goes to an endpoint
    of this server that it names in `_meta` — the selection and the view kept for a host
    that keeps none (selection.py, PUBLIC_URL + "/view"), and the report that the coach
    was shown (coach.py, PUBLIC_URL + "/coach")."""
    import re

    js = server.widget._WIDGET_JS
    # Any call spelled `fetch(` — bare, `window.fetch(`, `globalThis.fetch (` — but not
    # the widget's own prefetch(…) of the deck's next pictures, which loads an <img>.
    calls = re.findall(r"(?<!pre)fetch\s*\(\s*([\w.]+)", js)
    assert calls == ["selCap.url", "selCap.view", "selCap.view", "coachMeta.url"], calls
    assert 'selection.PUBLIC_URL + "/coach"' in open(server.coach.__file__).read()
    assert '"view_url": PUBLIC_URL + "/view"' in open(server.selection.__file__).read()
    assert not re.search(r"XMLHttpRequest|sendBeacon|EventSource|WebSocket", js)


# ── The same grid over HTTP (/widget) ────────────────────────────────────────

def _policy(header: str) -> dict[str, list[str]]:
    directives = {}
    for directive in header.split(";"):
        words = directive.split()
        if words:
            directives[words[0]] = words[1:]
    return directives


def _widget_csp() -> dict[str, list[str]]:
    from starlette.testclient import TestClient

    with TestClient(server.build_server().http_app()) as client:
        response = client.get("/widget")
    assert response.status_code == 200
    return _policy(response.headers["content-security-policy"])


def test_the_http_widget_allows_what_the_resource_declares(previews_on):
    """The page a browser shows is the grid a host shows: its CSP allows each origin for
    the same use as the resource's — pictures from the thumbnail CDN, `fetch` to this
    server alone."""
    import asyncio

    declared = asyncio.run(_grid_meta())["ui"]["csp"]
    csp = _widget_csp()
    assert previews.THUMB_ORIGIN not in csp["connect-src"]
    assert csp["connect-src"] == declared["connectDomains"] == [server.MCP_PUBLIC_URL.rstrip("/")]
    assert csp["img-src"] == [*declared["resourceDomains"], "data:", "blob:"]
    assert csp["media-src"] == declared["resourceDomains"]
    assert previews.THUMB_ORIGIN in csp["img-src"]


def test_without_a_thumbnail_cdn_the_http_widget_names_no_empty_origin():
    csp = _widget_csp()
    assert csp["img-src"] == ["data:", "blob:"]
    assert csp["media-src"] == ["'none'"]
    assert csp["connect-src"] == [server.MCP_PUBLIC_URL.rstrip("/")]
