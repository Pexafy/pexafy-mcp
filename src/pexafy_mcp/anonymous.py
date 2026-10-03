"""Who is asking, when nobody signed in.

This server is the only place that can answer that question. The host's `_meta` — the
one field where ChatGPT names the person it is acting for — exists in the JSON-RPC body
and nowhere else: not in the access log, not in anything the API can see. So identity
is resolved here and travels to the API as a signed statement (`X-Pexafy-Principal`),
which the API verifies and refuses to take on trust.

**Nothing here is a decision about policy.** Which identity sources are acceptable, in
what order, and whether an unattested one counts, are settings — because the honest
answer differs per host and will change:

  * **ChatGPT / Codex** send `openai/subject`, an anonymized per-person id (measured on
    production: 38 of 38 tool calls). It is exactly what is needed — and it is a header
    a script can also type, so it is only worth anything when the request came from an
    IP OpenAI publishes as its own. That is what `attest_openai` checks.
  * **Claude.ai** sends no identity at all, and every request leaves one network
    (160.79.104.0/21). Counting it by IP puts every Claude user in one bucket. The
    `mcp-session` source is the alternative: the session id is minted by THIS server,
    which is the one identifier a client cannot choose — at the cost that a client
    free to open a new session is free to open a new budget.
  * **Programmatic agents** call from their own address, where `ip` is both stable and
    meaningful.

Configuration (all optional, all read at import):

    PEXAFY_ANON_ENABLED        0     serve callers with no credential at all
    PEXAFY_ANON_SOURCES              ordered, comma-separated: openai-subject,ip
    PEXAFY_ANON_SECRET               HMAC key shared with the API (required when enabled);
                                     also the key the caller's digests are made with
    PEXAFY_ANON_TTL            300   seconds a signed principal stays valid
    PEXAFY_ANON_SHARED_NETWORKS      networks where an address names a crowd, not a person
                                     (default: Claude.ai's 160.79.104.0/21)
    PEXAFY_ANON_ATTEST_OPENAI  1     only trust openai/subject from a published OpenAI IP
    PEXAFY_ANON_OPENAI_FEED          URL of that IP list
    PEXAFY_ANON_FEED_TTL       3600  seconds between refreshes of the list
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import time

import httpx
from fastmcp.server.middleware import Middleware

from . import observe, store, tooling
from .env import flag

logger = logging.getLogger("pexafy.mcp.anonymous")

# ── Settings ─────────────────────────────────────────────────────────────────

ENABLED = flag("PEXAFY_ANON_ENABLED", False)
SECRET = os.environ.get("PEXAFY_ANON_SECRET", "")
TTL = int(os.environ.get("PEXAFY_ANON_TTL", "300") or 300)

# Default order: the strongest identity first, then the address. `mcp-session` is NOT
# in the default list — it identifies a conversation rather than a person, and a client
# can mint a new one at will, so turning it on is a deliberate trade an operator makes
# for a host that offers nothing better.
SOURCES = [
    s.strip()
    for s in os.environ.get("PEXAFY_ANON_SOURCES", "openai-subject,ip").split(",")
    if s.strip()
]

# The range Anthropic's servers call from: "the stable IP addresses that Anthropic uses
# for outbound requests" (platform.claude.com/docs/en/api/ip-addresses), the MCP
# connections of Claude.ai included. Also how hosts.is_claude tells a Claude host that
# does not name itself.
ANTHROPIC_OUTBOUND = "160.79.104.0/21"

# Networks where an address names a crowd, not a person. Claude.ai is the case that
# matters: it sends no identity at all and every one of its requests leaves
# ANTHROPIC_OUTBOUND, so counting by IP would put every Claude user on the planet on one
# budget — the first few would spend it and the rest would meet a refusal they cannot
# act on.
#
# So `ip` declines to name anyone here, no identity is resolved, and the caller gets
# the 401 that makes their client offer the OAuth sign-in. Decided 2026-09-13: Claude
# keeps OAuth rather than sharing a bucket.
SHARED_NETWORKS = [
    n.strip()
    for n in os.environ.get(
        "PEXAFY_ANON_SHARED_NETWORKS", ANTHROPIC_OUTBOUND
    ).split(",")
    if n.strip()
]

ATTEST_OPENAI = flag("PEXAFY_ANON_ATTEST_OPENAI", True)
OPENAI_FEED_URL = os.environ.get(
    "PEXAFY_ANON_OPENAI_FEED", "https://openai.com/chatgpt-connectors.json"
)
FEED_TTL = int(os.environ.get("PEXAFY_ANON_FEED_TTL", "3600") or 3600)
# Seconds to wait after a failed refresh: the fetch runs inside a request, and a feed
# that is down must not cost every message a timeout.
FEED_RETRY = 60

# Where the host puts the person's id, in the body and in the headers respectively.
OPENAI_SUBJECT_META = "openai/subject"
OPENAI_SUBJECT_HEADER = "x-openai-subject"

PRINCIPAL_HEADER = "X-Pexafy-Principal"


class Identity:
    """A resolved caller: what names them, where that name came from, and whether the
    host's own network vouched for it."""

    __slots__ = ("subject", "source", "client", "attested")

    def __init__(self, subject: str, source: str, client: str, attested: bool):
        self.subject = subject
        self.source = source
        self.client = client
        self.attested = attested

    def __repr__(self) -> str:  # pragma: no cover — logs use the digest, not this
        return f"Identity(source={self.source!r}, client={self.client!r}, attested={self.attested})"


def is_configured() -> bool:
    """Anonymous access needs a secret; without one the API would reject every
    principal we signed, so the feature stays off rather than half-on."""
    if not ENABLED:
        return False
    if not SECRET:
        logger.warning(
            "PEXAFY_ANON_ENABLED is set but PEXAFY_ANON_SECRET is empty — "
            "anonymous access stays off (the API would reject unsigned principals)."
        )
        return False
    return True


# ── The OpenAI IP list ───────────────────────────────────────────────────────

class _IPFeed:
    """The address ranges a host publishes as its own, refreshed on a TTL.

    A failure to refresh is not a failure to serve: the last good list is kept. Until
    one has loaded, `loaded` is False and attest_openai answers "cannot tell" rather
    than "not attested" — see there.
    """

    def __init__(self, url: str, ttl: int):
        self._url = url
        self._ttl = ttl
        self._networks: list = []
        self._fetched_at = 0.0
        self._ever_loaded = False
        self._retry_at = float("-inf")
        # One refresh at a time; callers arriving meanwhile use the list in hand.
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._ever_loaded

    def _parse(self, payload) -> list:
        """Accept the shapes a published IP list comes in: a bare list of prefixes, or
        an object with a `prefixes` array of `{ipv4Prefix}` / `{ipv6Prefix}` entries."""
        raw: list[str] = []
        if isinstance(payload, list):
            raw = [str(x) for x in payload]
        elif isinstance(payload, dict):
            entries = payload.get("prefixes") or payload.get("ranges") or []
            for entry in entries:
                if isinstance(entry, str):
                    raw.append(entry)
                elif isinstance(entry, dict):
                    for key in ("ipv4Prefix", "ipv6Prefix", "ip_prefix", "prefix", "cidr"):
                        if entry.get(key):
                            raw.append(str(entry[key]))
                            break
        networks = []
        for prefix in raw:
            try:
                networks.append(ipaddress.ip_network(prefix.strip(), strict=False))
            except ValueError:
                logger.debug("Ignoring unparseable prefix in %s: %r", self._url, prefix)
        return networks

    async def refresh(self, client: httpx.AsyncClient | None = None) -> None:
        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=10)
        try:
            resp = await client.get(self._url)
            resp.raise_for_status()
            networks = self._parse(resp.json())
        except (httpx.HTTPError, ValueError) as exc:
            # Said out loud: a silently stale list would keep attesting addresses a host
            # has since given up, and nothing would show it.
            logger.warning("Could not refresh the IP list at %s: %s", self._url, exc)
            self._retry_at = time.monotonic() + FEED_RETRY
            return
        finally:
            if owns_client:
                await client.aclose()

        if not networks:
            logger.warning("The IP list at %s parsed to nothing — keeping the previous one",
                           self._url)
            self._retry_at = time.monotonic() + FEED_RETRY
            return
        self._networks = networks
        self._fetched_at = time.monotonic()
        self._ever_loaded = True
        logger.info("Loaded %d prefixes from %s", len(networks), self._url)

    def stale(self) -> bool:
        now = time.monotonic()
        if now < self._retry_at:
            return False
        # Never loaded counts as stale: the monotonic clock starts at boot on Linux, so
        # comparing with 0.0 would wait for the host's uptime to pass the TTL.
        return not self._ever_loaded or now - self._fetched_at > self._ttl

    async def refresh_if_stale(self) -> None:
        if not self.stale() or self._lock.locked():
            return
        async with self._lock:
            await self.refresh()

    def contains(self, ip: str) -> bool:
        if not ip or not self._networks:
            return False
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in self._networks)


openai_feed = _IPFeed(OPENAI_FEED_URL, FEED_TTL)


async def attest_openai(ip: str) -> bool | None:
    """Is *ip* one OpenAI publishes? True, False — or None for "cannot tell".

    Three answers rather than two, because the two failures are not the same thing:

      * **False** — the list is loaded and this address is not in it. Someone is
        presenting a subject from an address OpenAI does not own, which is exactly what
        attestation is for.
      * **None** — the list has never loaded (the feed was unreachable at start-up).
        Nothing is known about this address, and saying "not attested" would be a
        claim we cannot make. With `require_attested` on, that claim would refuse every
        ChatGPT caller for as long as openai.com is unreachable from this host —
        turning someone else's outage into ours. The caller falls through to the next
        source instead: a shared budget by address, which is a degradation, not a
        blackout.
    """
    if not ATTEST_OPENAI:
        # Attestation turned off: the subject is taken at face value. Legitimate on a
        # network only the connector can reach; a hole on the public internet.
        return True
    await openai_feed.refresh_if_stale()
    if not openai_feed.loaded:
        return None
    return openai_feed.contains(ip)


# ── Resolution ───────────────────────────────────────────────────────────────

def _parse_networks(prefixes: list[str]) -> list:
    parsed = []
    for prefix in prefixes:
        try:
            parsed.append(ipaddress.ip_network(prefix, strict=False))
        except ValueError:
            logger.warning("Ignoring unparseable network in PEXAFY_ANON_SHARED_NETWORKS: %r",
                           prefix)
    return parsed


_SHARED = _parse_networks(SHARED_NETWORKS)


def is_shared_network(ip: str) -> bool:
    """True when this address stands for many people rather than one."""
    if not ip or not _SHARED:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in _SHARED)


def client_ip(headers: dict) -> str:
    """The caller's address as seen before our own proxies.

    CF-Connecting-IP first: mcp.pexafy.com is served through Cloudflare, which sets it
    (the socket address is Cloudflare's). Then X-Real-IP, then the LEFTMOST
    X-Forwarded-For entry: the original client as the first proxy saw it, the entries
    to its right being the proxies in between. None of the three can be told apart from
    a value the client wrote itself: they are only as good as the rule that the origin
    takes traffic from its own proxies alone.
    """
    for header in ("cf-connecting-ip", "x-real-ip"):
        value = (headers.get(header) or "").strip()
        if value:
            return value
    forwarded = (headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return ""


async def resolve(headers: dict, meta: dict, client_name: str = "") -> Identity | None:
    """Walk the configured sources in order and return the first identity found.

    None means no configured source could name this caller — the request is then
    refused rather than pooled onto a shared counter, which is the whole reason the
    sources are a list and not an if/else.
    """
    ip = client_ip(headers)

    for source in SOURCES:
        if source == "openai-subject":
            subject = str(meta.get(OPENAI_SUBJECT_META) or headers.get(OPENAI_SUBJECT_HEADER) or "")
            if subject:
                attested = await attest_openai(ip)
                if attested is None:
                    # Cannot tell — see attest_openai. Fall through rather than assert
                    # something false about this caller.
                    logger.warning(
                        "openai/subject present but the published IP list has never "
                        "loaded — falling through to the next source"
                    )
                    continue
                if not attested:
                    logger.info(
                        "openai/subject presented from an address outside the published "
                        "list — passing it on as unattested"
                    )
                return Identity(subject, "openai-subject", client_name or "chatgpt", attested)

        elif source == "mcp-session":
            session = str(headers.get("mcp-session-id") or "")
            if session:
                # Minted by this server, so a client cannot choose it — but it can ask
                # for another one, which is why this source is opt-in.
                return Identity(session, "mcp-session", client_name or "unknown", True)

        elif source == "ip":
            if ip and not is_shared_network(ip):
                return Identity(ip, "ip", client_name or "unknown", True)
            if ip:
                logger.info(
                    "address is in a shared network — not treating it as an identity"
                )

        else:
            logger.warning("Unknown identity source %r in PEXAFY_ANON_SOURCES", source)

    return None


# ── Signing ──────────────────────────────────────────────────────────────────
# Byte-for-byte the envelope `src/apis/principals.py` verifies on the Pexafy side: the
# payload's fields, its compact sorted JSON in base64url, the HMAC-SHA256 cut to 32 hex.
# Each side pins it with a frozen vector of its own (tests/test_anonymous.py here,
# tests/test_anonymous_principal.py there), run against its own code only: a change here
# fails no test there, and the vector there is updated by hand. It is behind today: it
# still carries `s` as derived before it was keyed (`ANON:6e491b…`; this server signs
# `ANON:6359ee…`), as does the API's twin signer, `sign_principal`. Both are to be
# aligned in the pexafy repository. Production is not affected: the API verifies the
# envelope and takes `s` as it comes.
#
# The API reads `s` as an opaque string — the counter it keys is `ANON:<s>` — so how `s`
# is derived is this server's business alone, and changing it changes nothing there but
# the counters: every anonymous caller starts again from zero, once.

# Domain labels of the two digests an anonymous caller gets, both keyed by the secret.
# Distinct, so that one cannot be computed from the other; versioned, so that a change
# of derivation is a change of label rather than a silent collision with the old one.
SUBJECT_DOMAIN = "pexafy-anon-subject:v1:"
LOG_DOMAIN = "pexafy-anon-log:v1:"

# The key of the log digest when no secret is configured — which, in production, is
# never: without a secret nobody is served anonymously, and no identity exists to log.
# Random per process rather than absent, so that even then the digest stays a keyed one.
_UNCONFIGURED_KEY = secrets.token_bytes(32)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def subject_digest(identity: Identity, secret: str) -> str:
    """The name a caller is counted under: HMAC-SHA256 of its source and subject, keyed
    by the secret, first 24 hex.

    Keyed, not a plain hash. A subject is often an IPv4 address, and there are only 2^32
    of those: `sha256("ip:<address>")` gives the address back to anyone who tries them
    all — the counters, the API's call log and every principal on the wire would have
    carried the address in all but name. Under a key only this server and the API hold,
    the digest names the caller and says nothing about them.
    """
    message = f"{SUBJECT_DOMAIN}{identity.source}:{identity.subject}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:24]


def sign(identity: Identity, *, secret: str = "", ttl: int = 0, now: float | None = None) -> str:
    secret = secret or SECRET
    if not secret:
        raise ValueError("refusing to sign an anonymous principal without a secret")
    issued = int(now if now is not None else time.time())
    payload = {
        "v": 1,
        "s": subject_digest(identity, secret),
        "c": identity.client or "unknown",
        "a": 1 if identity.attested else 0,
        "src": identity.source,
        "x": issued + max(1, int(ttl or TTL)),
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def digest(identity: Identity) -> str:
    """A short, stable label for logs and for the selection store — never the subject,
    and never a plain hash of it.

    Keyed like the subject digest, for the same reason (an address is a small space to
    search), under a label of its own: the log line cannot be joined to the API's
    counter, and neither can be computed from the other.
    """
    key = SECRET.encode() if SECRET else _UNCONFIGURED_KEY
    message = f"{LOG_DOMAIN}{identity.source}:{identity.subject}"
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()[:12]


# ── Carrying the identity through one request ────────────────────────────────
# Resolution happens in an MCP middleware, which is the only place the host's `_meta`
# is readable; the outgoing HTTP hook needs it several layers later. A ContextVar is
# what connects the two without either one knowing about the other.

current: contextvars.ContextVar["Identity | None"] = contextvars.ContextVar(
    "pexafy_anonymous_identity", default=None
)

# A bearer nobody can present. FastMCP refuses any request without an `Authorization`
# header before a single line of ours runs (RequireAuthMiddleware), so serving an
# anonymous caller means giving the request one — and this value must therefore be
# unguessable, or it would be a credential in its own right. It is generated per
# process and never leaves it.
SYNTHETIC_BEARER = "anon:" + secrets.token_urlsafe(32)


def is_synthetic_bearer(token: str) -> bool:
    # As bytes: `compare_digest` raises TypeError on a str holding non-ASCII characters,
    # and a bearer is whatever the client sent.
    return bool(token) and secrets.compare_digest(token.encode(), SYNTHETIC_BEARER.encode())


# What auth.PexafyResolveVerifier takes as a Pexafy API key when it is the bearer.
_API_KEY_PREFIX = b"pexafy_"


# ── Signing in where the host signs in on a 401 ──────────────────────────────
# ChatGPT attaches an account through a tool RESULT carrying `_meta["mcp/www_authenticate"]`
# (linking.py). The other hosts — VS Code, Cursor, Claude Code, any client of the MCP
# authorization spec — know only the 401: they sign in when a request is refused with
# `WWW-Authenticate`, and ignore that `_meta`. Served anonymously, such a host never
# meets a 401, so asking to connect (the grid's Sign in, or the model calling
# connect_account) did nothing at all. For them, that one request goes on WITHOUT the
# synthetic bearer: FastMCP answers it 401, and the host runs its own sign-in.
SIGN_IN_BY_401 = flag("PEXAFY_SIGN_IN_BY_401", True)
# The name hosts call it by (linking.CONNECT_TOOL is its registry key).
_CONNECT_TOOL = tooling.PUBLIC_TOOL_NAMES["connect_account"]
# hosts.OPENAI_CLIENT_MARKERS and hosts.OPENAI_META_PREFIX, for the same reason.
_OPENAI_MARKERS = ("openai", "chatgpt")
_OPENAI_META_PREFIX = "openai/"
# A call of connect_account is a few hundred bytes. A body past this is something else,
# and is not read any further (an uploaded image can be megabytes).
_SIGN_IN_BODY_MAX = 16 * 1024


async def _read_body(receive, limit: int):
    """The messages read, to replay, and the whole body — or None when it runs past
    `limit` bytes or the stream says something other than `http.request`."""
    messages, chunks, size = [], [], 0
    while True:
        message = await receive()
        messages.append(message)
        if message.get("type") != "http.request":
            return messages, None
        chunk = message.get("body") or b""
        size += len(chunk)
        if size > limit:
            return messages, None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return messages, b"".join(chunks)


def _replay(messages, receive):
    """A `receive` that hands out what was already read, then reads on."""
    pending = list(messages)

    async def replayed():
        if pending:
            return pending.pop(0)
        return await receive()

    return replayed


def asks_to_sign_in(body: bytes | None, headers: dict) -> bool:
    """True for a call of connect_account — not the grid's `check_only` probe, which
    must never raise — from a host that is not ChatGPT.

    ChatGPT names itself in its User-Agent (`openai-mcp/…`), in the subject header, or
    with `openai/…` keys in the call's `_meta` (hosts.is_openai reads the same marks);
    any of them keeps its connect flow, which a 401 would break.
    """
    if not body:
        return False
    agent = (headers.get("user-agent") or "").lower()
    if headers.get(OPENAI_SUBJECT_HEADER) or any(m in agent for m in _OPENAI_MARKERS):
        return False
    try:
        message = json.loads(body)
    except ValueError:
        return False
    if not isinstance(message, dict) or message.get("method") != "tools/call":
        return False
    params = message.get("params")
    if not isinstance(params, dict) or params.get("name") != _CONNECT_TOOL:
        return False
    arguments = params.get("arguments")
    if isinstance(arguments, dict) and arguments.get("check_only"):
        return False
    meta = params.get("_meta")
    if isinstance(meta, dict) and any(str(k).startswith(_OPENAI_META_PREFIX) for k in meta):
        return False
    return True



# ── A sign-in that holds — and lets go ───────────────────────────────────────
# The call after the 401 comes back with the token on the SAME session, and the SDK
# refuses it: a session only serves the credential that created it (mcp's
# StreamableHTTPSessionManager answers "Session not found", 404). The editor then opens
# a new session — and an editor sends its token only after a 401, so the new session
# would be anonymous again and the account never attached (measured in VS Code,
# 2026-09-30). So the sign-in is remembered for that editor on that machine, the
# machine as a digest of its address (store.py), and its session starts are answered
# 401: the editor sends the token it holds.
#
# Asked, not forced. A start answered 401 is noted (`asked`); the editor coming back
# WITH a token clears the note — it is signed in. Coming back WITHOUT one — signed out,
# or the prompt declined — means there is no account any more: the hold is dropped and
# it is served anonymously again (2026-10-01: "even after I disconnect, it insists on
# an account"). Another editor on the same machine keeps being served anonymously,
# unless it starts within the minutes after a sign-in asked from the grid.
# The scope key that tells unauthorized.py which of the two sign-in 401s this is, so the
# words a host hands the model when the person declines say what to do next.
SIGN_IN_SCOPE_KEY = "pexafy.sign_in"
SIGN_IN_PENDING_TTL = 15 * 60
SIGNED_IN_TTL = 180 * 24 * 3600
# Long: what clears an ask is the token coming back, not the clock.
ASKED_TTL = 30 * 24 * 3600
# hosts.CLAUDE_CLIENT_MARKERS: Claude has no anonymous access, nothing to hold for it.
_CLAUDE_MARKERS = ("claude", "anthropic")


def _machine(ip: str) -> str:
    """A digest of the caller's address, keyed so the store cannot be read back into
    addresses (an address is a small space to search)."""
    key = SECRET.encode() if SECRET else _UNCONFIGURED_KEY
    return hmac.new(key, f"signin:{ip}".encode(), hashlib.sha256).hexdigest()[:20]


def initializing_client(body: bytes | None, headers: dict) -> str:
    """The name an `initialize` gives, lowercased and trimmed, or "" for any other
    message — and for ChatGPT, which keeps its own connect flow, and Claude, which is
    never served anonymously."""
    if not body:
        return ""
    agent = (headers.get("user-agent") or "").lower()
    if headers.get(OPENAI_SUBJECT_HEADER) or any(m in agent for m in _OPENAI_MARKERS):
        return ""
    try:
        message = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(message, dict) or message.get("method") != "initialize":
        return ""
    params = message.get("params")
    info = params.get("clientInfo") if isinstance(params, dict) else None
    name = str(info.get("name") or "") if isinstance(info, dict) else ""
    name = " ".join(name.lower().split())[:64]
    if not name or any(m in name for m in _OPENAI_MARKERS + _CLAUDE_MARKERS):
        return ""
    return name


def _editor(ip: str, client: str) -> str:
    return f"{_machine(ip)}:{hashlib.sha256(client.encode()).hexdigest()[:16]}"


async def remember_sign_in(ip: str) -> None:
    """A sign-in was asked from this machine: its next session must sign in too."""
    if ip:
        await store.put(f"signin:pending:{_machine(ip)}", SIGN_IN_PENDING_TTL)


async def must_sign_in(ip: str, client: str) -> bool:
    """For a session start WITHOUT a token: True when this editor, on this machine, is
    to be asked to sign in (answered 401); False when it is served anonymously."""
    if not (ip and client):
        return False
    editor = _editor(ip, client)
    held, asked = f"signin:editor:{editor}", f"signin:asked:{editor}"
    if await store.has(held):
        if await store.has(asked):
            # Asked at its last start, and back without a token: signed out.
            await store.drop(held)
            await store.drop(asked)
            logger.info("An editor asked to sign in came back without a token: "
                        "served anonymously again")
            return False
        await store.put(asked, ASKED_TTL)
        await store.put(held, SIGNED_IN_TTL)            # renewed at every start
        return True
    pending = f"signin:pending:{_machine(ip)}"
    if await store.has(pending):
        await store.drop(pending)
        await store.put(held, SIGNED_IN_TTL)
        await store.put(asked, ASKED_TTL)
        return True
    return False


async def signed_in_start(ip: str, client: str) -> None:
    """A session start WITH an OAuth token: this editor uses an account on this machine.
    The ask, if there was one, is answered; the hold is set or renewed."""
    if not (ip and client):
        return
    editor = _editor(ip, client)
    await store.drop(f"signin:asked:{editor}")
    await store.put(f"signin:editor:{editor}", SIGNED_IN_TTL)

class AllowAnonymous:
    """ASGI middleware: give a credential-less request the synthetic bearer.

    Without it the request is refused by FastMCP's auth middleware before anything
    here can look at it. With it, the request reaches the server as "authenticated" by
    a token only this process knows, and the identity that actually matters is resolved
    from the body one layer further in.

    Requests that already carry an `Authorization` header are left untouched: an OAuth
    session or a client-supplied API key takes the normal auth path. A Pexafy key sent
    in `x-api-key` instead is copied to `Authorization: Bearer`, the only header the
    auth layer reads, so the key path resolves it as the caller's own credential.
    """

    def __init__(self, app, *, sign_in_by_401: bool = False):
        self.app = app
        # Only where the auth layer answers a request without a credential with a 401,
        # that is where OAuth is on (server.OAUTH_ENABLED): elsewhere the request would
        # simply run without an identity.
        self.sign_in_by_401 = sign_in_by_401 and SIGN_IN_BY_401

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        bearer = next((v for k, v in headers if k == b"authorization"), None)
        if bearer is not None:
            if (self.sign_in_by_401 and scope.get("method") == "POST"
                    and not bearer.split(b" ")[-1].startswith(_API_KEY_PREFIX)):
                # An OAuth session start: the editor signed in (signed_in_start).
                messages, body = await _read_body(receive, _SIGN_IN_BODY_MAX)
                receive = _replay(messages, receive)
                lowered = {k.decode("latin-1").lower(): v.decode("latin-1")
                           for k, v in headers}
                client = initializing_client(body, lowered)
                if client:
                    await signed_in_start(client_ip(lowered), client)
            await self.app(scope, receive, send)
            return

        # Before the anonymous check: with OAuth on, the key would otherwise meet a
        # 401 (anonymous access off) or be swapped for the service key (on).
        key = next((value.strip() for name, value in headers if name == b"x-api-key"), b"")
        if key.startswith(_API_KEY_PREFIX):
            scope = dict(scope)
            scope["headers"] = list(headers) + [(b"authorization", b"Bearer " + key)]
            await self.app(scope, receive, send)
            return

        if not is_configured():
            await self.app(scope, receive, send)
            return

        if not self._could_be_identified(headers):
            # Nothing here can name this caller, and nothing later will either: the 401
            # FastMCP is about to answer is what makes their client offer the OAuth
            # sign-in. Letting them in and failing at tool-call time would replace a
            # sign-in button with an error message.
            await self.app(scope, receive, send)
            return

        if self.sign_in_by_401 and scope.get("method") == "POST":
            messages, body = await _read_body(receive, _SIGN_IN_BODY_MAX)
            receive = _replay(messages, receive)
            lowered = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers}
            if asks_to_sign_in(body, lowered):
                await remember_sign_in(client_ip(lowered))
                logger.info("Sign-in asked by a host that signs in on a 401: "
                            "the call goes on without the anonymous bearer")
                await self.app({**scope, SIGN_IN_SCOPE_KEY: "connect"}, receive, send)
                return
            client = initializing_client(body, lowered)
            if client and await must_sign_in(client_ip(lowered), client):
                logger.info("An editor that signed in on this machine opens a session: "
                            "401, so that it sends its token")
                await self.app({**scope, SIGN_IN_SCOPE_KEY: "start"}, receive, send)
                return

        scope = dict(scope)
        scope["headers"] = list(headers) + [
            (b"authorization", f"Bearer {SYNTHETIC_BEARER}".encode())
        ]
        await self.app(scope, receive, send)

    @staticmethod
    def _could_be_identified(raw_headers) -> bool:
        """Decided on headers alone, which is all an ASGI middleware has.

        A host that puts the person's id in the BODY (ChatGPT's `openai/subject`) is not
        ruled out here — its address is not a shared one, so `ip` would name it and the
        real resolution happens later with the body in hand. What this rules out is the
        caller whose only possible identity is an address that stands for a crowd.
        """
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in (raw_headers or [])}
        if headers.get(OPENAI_SUBJECT_HEADER):
            return True
        if "mcp-session" in SOURCES and headers.get("mcp-session-id"):
            return True
        if "ip" not in SOURCES:
            # Without the address as a fallback, only a host-supplied id can identify
            # anyone, and this middleware cannot see the body. Let it through and let
            # resolution decide.
            return True
        ip = client_ip(headers)
        return bool(ip) and not is_shared_network(ip)


class ResolveIdentity(Middleware):
    """MCP middleware: work out who an anonymous caller is, once per message.

    Only for callers that arrived with no credential of their own — an OAuth user or a
    key holder is already identified, and giving them an anonymous principal too would
    be handing the API two answers to one question.
    """

    async def on_message(self, context, call_next):
        token = current.set(None)
        try:
            if is_configured() and _is_anonymous_request():
                try:
                    identity = await resolve(
                        _incoming_headers(),
                        observe.meta_of(context),
                        observe.client_of(context),
                    )
                except Exception as exc:  # noqa: BLE001 — never cost a search
                    logger.warning("Could not resolve an anonymous identity: %s", exc)
                    identity = None
                if identity is not None:
                    current.set(identity)
                    logger.info(
                        "anonymous caller id=%s source=%s attested=%s client=%s",
                        digest(identity), identity.source, identity.attested, identity.client,
                    )
                else:
                    logger.info("anonymous caller could not be identified by any configured source")
            return await call_next(context)
        finally:
            current.reset(token)


# `get_http_headers()` hides `authorization` by default — a sensible default that is
# wrong for us twice over: it is the header AllowAnonymous injects, and reading it is
# how this middleware tells an anonymous caller from an authenticated one. Asking for
# it explicitly is what `include` is for (server.py does the same for the key path).
_HEADERS_NEEDED = {
    "authorization",
    "cf-connecting-ip",
    "x-real-ip",
    "x-forwarded-for",
    OPENAI_SUBJECT_HEADER,
    "mcp-session-id",
    # hosts.is_codex: Codex says "(Codex)" there, and nowhere else.
    "user-agent",
}


def _incoming_headers() -> dict:
    """The headers identity resolution and hosts.py need, lowercased, or {} under stdio."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers(include=_HEADERS_NEEDED) or {}
        return {k.lower(): v for k, v in headers.items()}
    except Exception:  # noqa: BLE001 — no HTTP request in scope (stdio, unit call)
        return {}


def _is_anonymous_request() -> bool:
    """True when the only credential on this request is the one we injected."""
    auth = _incoming_headers().get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    return is_synthetic_bearer(auth[7:].strip())


def attach_principal(request) -> bool:
    """Put the signed principal on an outgoing API request. True when one was added."""
    identity = current.get()
    if identity is None:
        return False
    try:
        request.headers[PRINCIPAL_HEADER] = sign(identity)
    except ValueError as exc:
        # No secret: the API would reject it anyway, and a request with the service key
        # and no principal is refused there on purpose. Say why rather than send it.
        logger.warning("Not attaching an anonymous principal: %s", exc)
        return False
    return True
