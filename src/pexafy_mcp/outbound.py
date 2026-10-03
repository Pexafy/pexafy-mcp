"""What a request to the Pexafy API carries — and nothing else.

The API learns who is calling from what this server puts on the request: the caller's
own key, or the service key with a signed principal (anonymous.py), and the analytics
tag `X-Source`, which it checks against the key's scope. Nothing it needs comes from the
request a host sent this server.

Yet FastMCP's generated tool — the text search — copied that request's headers onto the
one it sends (`OpenAPITool.run`: every header `get_http_headers()` does not exclude): the
caller's address as each proxy reported it (`cf-connecting-ip`, `x-forwarded-for`,
`x-real-ip`), its country and Cloudflare's ray, ChatGPT's `x-openai-subject` and
`x-openai-session`, cookies, `origin`, `referer`, `accept-language`, trace ids — and any
header a client cared to add, the API's internal ones included (`x-pexafy-probe` leaves
a key's dates of use as they were). The API writes the address of every call to its
call log (`api_key_calls.client_ip`, 90 days, shown on the staff screen of each key): the
person's address landed there, which the README says never travels.

So each request is rebuilt on an allow-list (`restrict`): the headers httpx writes for
that request — its `host`, and its body's `content-type`, `content-length` or
`transfer-encoding` — and the client's own defaults, with the client's values: `accept`,
`accept-encoding`, `connection`, its `user-agent`, the key and `X-Source`. Then comes
what this server means to send: the caller's address under a name that reveals nothing
(below), and, in the hook after, the caller's credential or the signed principal
(server._forward_client_key).

An address that names nobody
----------------------------
One thing in the API counts by address: its limit per address and per path — 60
searches a minute, 100 calls elsewhere (RateLimitMiddleware, `src/apis/middleware.py`) —
which takes the address from `cf-connecting-ip`, then the first `x-forwarded-for` entry,
whoever sent them, then the socket's peer. With no address on the request, every MCP
call would count under the one address of this server's container: every person, every
assistant, 60 searches a minute between them. The text search counted per caller address
until now; the by-image search, the file's metadata and the allowance already counted
under the container's.

So the request names its caller in `X-Forwarded-For` by a pseudonym (`pseudonym`): an
HMAC-SHA256 of the address under a key this process draws at start and keeps nowhere,
written as an address of the unique-local network PSEUDONYM_NETWORK (RFC 4193 — never a
public address, and still an address to whatever parses one; it fits the 45 characters
of the API's column). The API keeps one count per caller, for every call now, and its
log holds a name nobody can turn back into the address: not the API, which has no key,
not this server after a restart. A restart draws a new key and new names, which costs
nothing to a limit that counts one minute.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets

import httpx

from . import anonymous

# The headers httpx writes for the request it builds: where it goes, and what its body
# is. Kept as httpx wrote them.
TRANSPORT_HEADERS = frozenset({"host", "content-type", "content-length", "transfer-encoding"})

# Where the caller's pseudonym goes: the header the API reads an address from when the
# request did not come through Cloudflare, which is the case on the network between them.
ADDRESS_HEADER = "X-Forwarded-For"

# fd + 70 65 78 61 66, "pexaf" in ASCII: one recognisable /48 of the unique-local range.
PSEUDONYM_NETWORK = ipaddress.IPv6Network("fd70:6578:6166::/48")
_HOST_BITS = PSEUDONYM_NETWORK.max_prefixlen - PSEUDONYM_NETWORK.prefixlen
_PSEUDONYM_DOMAIN = b"pexafy-api-address:v1:"
# Drawn per process and never written anywhere: nobody holds what would reverse a name.
_PSEUDONYM_KEY = secrets.token_bytes(32)


def restrict(request: httpx.Request, defaults: httpx.Headers) -> list[str]:
    """Leave on *request* only TRANSPORT_HEADERS and the client's *defaults*, these with
    the client's values. Returns the names taken off, lowercased, for the log."""
    allowed = TRANSPORT_HEADERS | {name.lower() for name in defaults.keys()}
    dropped = sorted({name.lower() for name in request.headers.keys()} - allowed)
    for name in dropped:
        del request.headers[name]
    for name, value in defaults.items():
        request.headers[name] = value
    return dropped


def pseudonym(address: str) -> str:
    """The name the API is given for the caller at *address*: an address of
    PSEUDONYM_NETWORK, the same for the same address while this process lives."""
    text = (address or "").strip()
    try:
        parsed = ipaddress.ip_address(text)
        if parsed.version == 6 and parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped
        text = str(parsed)
    except ValueError:
        pass  # not an address: named all the same, so that it keeps one count
    digest = hmac.new(_PSEUDONYM_KEY, _PSEUDONYM_DOMAIN + text.encode("utf-8", "replace"),
                      hashlib.sha256).digest()
    return str(PSEUDONYM_NETWORK[int.from_bytes(digest, "big") >> (256 - _HOST_BITS)])


def name_the_caller(request: httpx.Request) -> None:
    """Put the caller's pseudonym on *request*, when the request this server is serving
    has an address — never under stdio."""
    address = anonymous.client_ip(anonymous._incoming_headers())
    if address:
        request.headers[ADDRESS_HEADER] = pseudonym(address)
