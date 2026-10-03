"""What a request to the Pexafy API carries: what this server puts on it, nothing a host sent.

FastMCP's generated search copied the incoming request's headers onto the request it
sends: the caller's address (`cf-connecting-ip`, `x-forwarded-for`, `x-real-ip`),
ChatGPT's `x-openai-*`, cookies, `origin`, `referer`… and the API writes the address of
every call to its call log for 90 days. Each request is now rebuilt on an allow-list,
and the caller's address reaches the API only as a pseudonym, for the one count it keeps
per address (outbound.py). Offline: ASGI in process, the API is an httpx.MockTransport.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json

import httpx
import pytest
from starlette.testclient import TestClient

from pexafy_mcp import anonymous, compat, outbound, server, tooling

CALLER_KEY = "pexafy_api_THE_CALLERS_OWN_KEY"
ADDRESS = "203.0.113.7"
SUBJECT = "v1/subject-of-one-person"
PHOTO_ID = "019e1ecb-0039-7da6-b1ca-987ee4d337c1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

# What a host's request carries once Cloudflare and Caddy are behind it, plus the API's
# own internal headers, which no caller may set through this server.
INCOMING = {
    "CF-Connecting-IP": ADDRESS,
    "X-Forwarded-For": f"{ADDRESS}, 172.70.1.1",
    "X-Real-IP": ADDRESS,
    "CF-IPCountry": "FR",
    "CF-Ray": "8c0ffee0123abcd-CDG",
    "User-Agent": "openai-mcp/1.0.0",
    "X-Openai-Subject": SUBJECT,
    "X-Openai-Session": "v1/session-of-one-conversation",
    "Cookie": "sessionid=a-cookie-of-the-person",
    "Origin": "https://chatgpt.com",
    "Referer": "https://chatgpt.com/c/one-conversation",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    "X-Pexafy-Probe": "1",
    "X-Service-Token": "a-guessed-service-token",
    "X-Pexafy-Proxy-Plan": "business",
    "Idempotency-Key": "replay-someone-elses-answer",
    "X-Pexafy-Principal": "a.forged-principal",
}
# None of these may appear in any header the API receives.
NEVER_SENT = (ADDRESS, "172.70.1.1", "openai-mcp", SUBJECT, "session-of-one-conversation",
              "a-cookie-of-the-person", "chatgpt.com", "8c0ffee0123abcd", "fr-FR",
              "0af7651916cd43dd", "a-guessed-service-token", "business",
              "replay-someone-elses-answer", "a.forged-principal")
# What a request to the API may carry: HTTP's own headers, the client's defaults, and
# what the server adds on purpose — the caller's credential, the signed principal of a
# caller without an account, and the caller's pseudonym.
ALLOWED = {"host", "accept", "accept-encoding", "connection", "user-agent", "x-api-key",
           "x-source", "content-type", "content-length", "transfer-encoding",
           "x-forwarded-for", "x-pexafy-principal"}


def _api_client_ip(headers) -> str:
    """The address the API counts a call under: `get_client_ip`, src/apis/client_ip.py
    (develop and feature/mcp_new_version alike), the socket's peer left out."""
    cf = headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    xff = headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() if xff else "<the socket's peer>"


@pytest.fixture
def api(monkeypatch):
    """The API, in memory: every request is kept; a search answers one photograph."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"success": True, "data": [{"photo_id": PHOTO_ID, "width": 10, "height": 10}]}
        return httpx.Response(200, json=body, headers={"content-type": "application/json"})

    monkeypatch.setattr(server.client, "_transport", httpx.MockTransport(respond))
    return seen


@pytest.fixture
def oauth_on(monkeypatch):
    """The resource server as production runs it; Django is never called (a Pexafy key
    as the bearer is resolved without it, and so is the synthetic one)."""
    monkeypatch.setattr(server, "OAUTH_ENABLED", True)
    monkeypatch.setattr(server, "OAUTH_RESOLVE_URL", "http://resolve.invalid/oauth/mcp/resolve")
    monkeypatch.setattr(server, "OAUTH_RESOLVE_SECRET", "resolve-secret")
    monkeypatch.setattr(server, "OAUTH_AS_URL", "http://as.invalid")
    monkeypatch.setattr(server, "MCP_PUBLIC_URL", "http://testserver")


def _calls(headers: dict, calls: list[tuple[str, dict]], client_name: str = "openai-mcp"):
    """Open a session over the HTTP transport with *headers* on every request, and make
    each tool call in turn."""
    base = {"Accept": "application/json, text/event-stream",
            "Content-Type": "application/json", **headers}

    def rpc(client, body, session=None):
        extra = {"mcp-session-id": session} if session else {}
        return client.post("/mcp", content=json.dumps(body), headers={**base, **extra})

    with TestClient(server.http_app(server.build_server())) as client:
        r = rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": client_name, "version": "1.0.0"}}})
        assert r.status_code == 200, r.text
        session = r.headers.get("mcp-session-id")
        rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
        for i, (name, arguments) in enumerate(calls, start=2):
            r = rpc(client, {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}}, session)
            assert r.status_code == 200, r.text
            assert '"isError":true' not in r.text.replace(" ", ""), r.text


# Every tool call that reaches the API: the text search (the generated tool, the one
# FastMCP copied headers onto), the by-image search from a catalogue photo and from bytes,
# the 0.4.12 similar tool ChatGPT still sends, and the account probe (the usage endpoint).
EVERY_CALL = [
    (tooling.PUBLIC_TOOL_NAMES["search_photos"], {tooling.PUBLIC_QUERY_PARAM: "a red bicycle"}),
    (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"], {"photo_id": PHOTO_ID}),
    (tooling.PUBLIC_TOOL_NAMES["search_photos_by_image"],
     {"image_base64": base64.b64encode(PNG).decode()}),
    (compat.SIMILAR_TOOL, {"photo_id": PHOTO_ID}),
    (tooling.PUBLIC_TOOL_NAMES["connect_account"], {"check_only": True}),
]


def test_fastmcp_copies_the_host_s_headers_onto_the_text_search(api, oauth_on, monkeypatch):
    """Why the allow-list exists: without it, the installed FastMCP hands the API the
    caller's address and ChatGPT's ids on the generated search (OpenAPITool.run)."""
    hooks = dict(server.client.event_hooks)
    hooks["request"] = [h for h in hooks["request"] if h is not server._only_what_the_api_needs]
    monkeypatch.setattr(server.client, "event_hooks", hooks)
    _calls({**INCOMING, "x-api-key": CALLER_KEY}, EVERY_CALL[:1])
    [request] = api
    for name in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip", "x-openai-subject",
                 "x-openai-session", "cookie", "origin", "referer", "x-pexafy-probe"):
        assert name in request.headers, name
    assert _api_client_ip(request.headers) == ADDRESS


def test_no_request_to_the_api_carries_what_the_host_sent(api, oauth_on):
    _calls({**INCOMING, "x-api-key": CALLER_KEY}, EVERY_CALL)
    paths = [(r.method, r.url.path) for r in api]
    assert paths == [("GET", "/api/v1/search/photos"), ("POST", "/api/v1/search/photos"),
                     ("POST", "/api/v1/search/photos"), ("POST", "/api/v1/search/photos"),
                     ("GET", "/api/v1/usage")]
    for request in api:
        names = set(request.headers.keys())
        assert names <= ALLOWED, (request.url.path, names - ALLOWED)
        sent = json.dumps(dict(request.headers))
        for value in NEVER_SENT:
            assert value not in sent, (request.url.path, value)
        # The caller's credential, the server's own tag and agent — never the host's.
        assert request.headers["x-api-key"] == CALLER_KEY
        assert request.headers["x-source"] == server.SOURCE_HEADER
        assert request.headers["user-agent"] == server.client.headers["user-agent"]
        assert not request.headers["user-agent"].startswith("openai")
        # No principal on a call with a key: the forged one did not ride along.
        assert anonymous.PRINCIPAL_HEADER not in request.headers
        assert "cf-connecting-ip" not in request.headers and "x-real-ip" not in request.headers
        # The address the API counts this call under is the caller's pseudonym.
        assert _api_client_ip(request.headers) == outbound.pseudonym(ADDRESS)
    # The image travels as the body, with the type and length httpx wrote for it.
    upload = api[2]
    assert upload.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert int(upload.headers["content-length"]) == len(upload.content)


def test_a_caller_without_an_account_reaches_the_api_as_a_signed_principal_only(
        api, oauth_on, monkeypatch):
    """The anonymous path: the service key, the principal the server signed — whose
    subject is a digest — and the pseudonym. Neither ChatGPT's subject nor the address."""
    secret = "outbound-test-secret"
    monkeypatch.setattr(anonymous, "ENABLED", True)
    monkeypatch.setattr(anonymous, "SECRET", secret)
    monkeypatch.setattr(anonymous, "SOURCES", ["openai-subject", "ip"])
    monkeypatch.setattr(anonymous, "ATTEST_OPENAI", False)
    incoming = {k: v for k, v in INCOMING.items() if k != "X-Pexafy-Principal"}
    _calls(incoming, EVERY_CALL[:2])
    assert len(api) == 2
    for request in api:
        assert set(request.headers.keys()) <= ALLOWED
        sent = json.dumps(dict(request.headers))
        for value in NEVER_SENT:
            assert value not in sent, (request.url.path, value)
        token = request.headers[anonymous.PRINCIPAL_HEADER]
        body, mac = token.split(".")
        assert mac == hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert payload["src"] == "openai-subject"
        assert payload["s"] == anonymous.subject_digest(
            anonymous.Identity(SUBJECT, "openai-subject", "chatgpt", True), secret)
        assert request.headers["x-api-key"] == server.API_KEY
        assert _api_client_ip(request.headers) == outbound.pseudonym(ADDRESS)


def test_the_api_still_counts_each_caller_apart(api, oauth_on):
    """The API's limit of 60 searches a minute is per address. Without one, every MCP
    call would share the server's: each caller keeps a count of its own, under a name
    that is not its address."""
    _calls({"CF-Connecting-IP": "203.0.113.7", "x-api-key": CALLER_KEY}, EVERY_CALL[:2])
    _calls({"CF-Connecting-IP": "198.51.100.23", "x-api-key": CALLER_KEY}, EVERY_CALL[:1])
    first, again, other = (_api_client_ip(r.headers) for r in api)
    assert first == again != other
    for name in (first, other):
        assert ipaddress.ip_address(name) in outbound.PSEUDONYM_NETWORK


async def test_without_an_http_request_there_is_no_address_to_name(fake_api):
    """stdio, or an in-process client: nothing to pass on, and no address is invented —
    the API counts the socket's peer, as before."""
    from fastmcp import Client

    async with Client(server.build_server()) as client:
        await client.call_tool(tooling.PUBLIC_TOOL_NAMES["search_photos"],
                               {tooling.PUBLIC_QUERY_PARAM: "a red bicycle"})
    [request] = fake_api.requests
    assert set(request.headers.keys()) <= ALLOWED - {"x-forwarded-for", "x-pexafy-principal"}


# ── The two pieces ────────────────────────────────────────────────────────────

def test_restrict_keeps_the_transport_headers_and_the_client_s_own_values():
    defaults = httpx.Headers({"user-agent": "the-server/1", "x-source": "MCP-Agent",
                              "x-api-key": "", "accept": "*/*"})
    request = httpx.Request("POST", "http://api.invalid/api/v1/search/photos",
                            files={"image": ("a.png", PNG, "image/png")},
                            headers={"User-Agent": "openai-mcp/1.0.0", "X-Source": "evil",
                                     "CF-Connecting-IP": ADDRESS, "Cookie": "a=b",
                                     "X-Openai-Subject": SUBJECT})
    content_type = request.headers["content-type"]
    dropped = outbound.restrict(request, defaults)
    assert dropped == ["cf-connecting-ip", "cookie", "x-openai-subject"]
    assert dict(request.headers) == {
        "host": "api.invalid", "content-length": str(len(request.read())),
        "content-type": content_type, "user-agent": "the-server/1", "x-source": "MCP-Agent",
        "x-api-key": "", "accept": "*/*"}


def test_the_pseudonym_names_an_address_without_revealing_it(monkeypatch):
    name = outbound.pseudonym(ADDRESS)
    parsed = ipaddress.ip_address(name)
    assert parsed in outbound.PSEUDONYM_NETWORK and parsed.is_private
    assert len(name) <= 45                     # api_key_calls.client_ip is VARCHAR(45)
    assert "203" not in name.replace(":", " ").split() and ADDRESS not in name
    # One name per address, the same each time; the address as an IPv6 mapping too.
    assert outbound.pseudonym(f" {ADDRESS} ") == name
    assert outbound.pseudonym(f"::ffff:{ADDRESS}") == name
    assert outbound.pseudonym("198.51.100.23") != name
    assert outbound.pseudonym("2001:db8::1") == outbound.pseudonym("2001:0db8:0:0::1")
    # Something that is not an address still gets one name, and keeps one count.
    assert outbound.pseudonym("unknown") == outbound.pseudonym("unknown") != name
    # Keyed by the process: no plain hash of the address, and new names after a restart.
    plain = int.from_bytes(hashlib.sha256(ADDRESS.encode()).digest(), "big") >> 176
    assert parsed != outbound.PSEUDONYM_NETWORK[plain]
    monkeypatch.setattr(outbound, "_PSEUDONYM_KEY", b"another process" * 2)
    assert outbound.pseudonym(ADDRESS) != name
