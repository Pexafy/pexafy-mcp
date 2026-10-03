"""Anonymous callers: who the server says they are, and what it refuses to say.

The one thing this file guards above all is the WIRE FORMAT. The token signed here is
verified by `src/apis/principals.py` in the Pexafy repo — two codebases, two deploys,
one format. A change to either that the other does not follow would not fail loudly;
it would fail as "every anonymous call is refused", in production, for reasons nothing
in the logs connects to a refactor. So both sides pin a frozen token (GOLDEN_* below,
and pexafy/tests/test_anonymous_principal.py there), each against its own code only:
nothing fails there when this side changes. A change to the payload, the digest or the
signature is copied there by hand, on purpose. The vector there is behind — it still
has `s` from before the digest was keyed — and is to be aligned in the pexafy repository.

    pytest tests/test_anonymous.py -v
"""
from __future__ import annotations

import base64
import json
import os
import unittest
from unittest.mock import patch

import httpx
import pytest

from pexafy_mcp import anonymous

# Naming: pytest collects a plain (non-unittest) test class only when its name STARTS
# with "Test" — a plain `SomethingTests` class is skipped without a word.
# `unittest.TestCase` subclasses are collected whatever they are called, hence the two
# styles below.

# ── The frozen interoperability vector ───────────────────────────────────────
GOLDEN_SECRET = "golden-vector-secret"
GOLDEN_SUBJECT = "sub_golden"
GOLDEN_SOURCE = "openai-subject"
GOLDEN_CLIENT = "chatgpt"
GOLDEN_ISSUED = 1_700_000_000
GOLDEN_TTL = 600
GOLDEN_TOKEN = (
    "eyJhIjoxLCJjIjoiY2hhdGdwdCIsInMiOiI2MzU5ZWUwMDFlMmMxZTVmZWIzZGZmNjciLCJzcmMiOiJvcGVu"
    "YWktc3ViamVjdCIsInYiOjEsIngiOjE3MDAwMDA2MDB9.f304214e82b566fd4e55502d4e46e2d1"
)
# What the API turns that token into. Pinned here too, because the counter key IS the
# budget: if this string changes, every anonymous caller silently starts from zero.
# It changed once, on purpose, when `s` went from sha256(source:subject) to an HMAC
# keyed by the secret (the vector before was "ANON:6e491b5d54ede000e08a3e26").
GOLDEN_PRINCIPAL_ID = "ANON:6359ee001e2c1e5feb3dff67"


def _identity(subject=GOLDEN_SUBJECT, source=GOLDEN_SOURCE, client=GOLDEN_CLIENT, attested=True):
    return anonymous.Identity(subject, source, client, attested)


class WireFormatTests(unittest.TestCase):
    def test_the_frozen_vector_still_signs_identically(self):
        """If this fails, the Pexafy API can no longer verify what this server signs."""
        token = anonymous.sign(
            _identity(), secret=GOLDEN_SECRET, ttl=GOLDEN_TTL, now=GOLDEN_ISSUED
        )
        self.assertEqual(token, GOLDEN_TOKEN)

    def test_the_payload_is_exactly_the_agreed_fields(self):
        body = GOLDEN_TOKEN.split(".")[0]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        self.assertEqual(
            payload,
            {
                "v": 1,
                "s": "6359ee001e2c1e5feb3dff67",
                "c": "chatgpt",
                "a": 1,
                "src": "openai-subject",
                "x": GOLDEN_ISSUED + GOLDEN_TTL,
            },
        )

    def test_the_counter_key_the_api_derives_is_pinned(self):
        body = GOLDEN_TOKEN.split(".")[0]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        self.assertEqual("ANON:" + payload["s"], GOLDEN_PRINCIPAL_ID)

    def test_the_subject_is_not_readable_from_the_token(self):
        self.assertNotIn(GOLDEN_SUBJECT, GOLDEN_TOKEN)

    def test_signing_without_a_secret_is_refused(self):
        with patch.object(anonymous, "SECRET", ""):
            with self.assertRaises(ValueError):
                anonymous.sign(_identity())

    def test_the_same_caller_signs_to_the_same_subject_digest(self):
        a = anonymous.sign(_identity(), secret="s", ttl=60, now=1)
        b = anonymous.sign(_identity(), secret="s", ttl=60, now=2)
        self.assertEqual(a.split(".")[0][:40], b.split(".")[0][:40])

    def test_a_different_source_yields_a_different_budget(self):
        by_ip = anonymous.sign(_identity(source="ip"), secret="s", ttl=60, now=1)
        by_sub = anonymous.sign(_identity(source="openai-subject"), secret="s", ttl=60, now=1)
        self.assertNotEqual(by_ip, by_sub)


class SaltedSubjectTests(unittest.TestCase):
    """`s` is an HMAC keyed by the secret, not a plain hash of the subject.

    A subject is often an IPv4 address: 2^32 candidates, and `sha256("ip:<address>")`
    gave every one of them back to whoever tried them all — in the API's counters, its
    call log, and every principal on the wire. The privacy policy promises a salted
    hash; this is the salt.
    """

    ADDRESS = _identity(subject="203.0.113.42", source="ip", client="unknown")

    def _s(self, token):
        body = token.split(".")[0]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["s"]

    def test_it_is_the_documented_hmac(self):
        """Written out independently, as the API side would have to write it."""
        import hashlib
        import hmac

        want = hmac.new(b"k1", b"pexafy-anon-subject:v1:ip:203.0.113.42",
                        hashlib.sha256).hexdigest()[:24]
        self.assertEqual(anonymous.subject_digest(self.ADDRESS, "k1"), want)
        self.assertEqual(self._s(anonymous.sign(self.ADDRESS, secret="k1", ttl=60, now=1)), want)

    def test_the_plain_hash_is_gone(self):
        import hashlib

        plain = hashlib.sha256(b"ip:203.0.113.42").hexdigest()[:24]
        self.assertNotEqual(anonymous.subject_digest(self.ADDRESS, "k1"), plain)

    def test_it_takes_the_secret_to_compute_it(self):
        """Without the key, trying every address yields nothing that matches."""
        a = anonymous.subject_digest(self.ADDRESS, "k1")
        self.assertEqual(a, anonymous.subject_digest(self.ADDRESS, "k1"))  # stable
        self.assertNotEqual(a, anonymous.subject_digest(self.ADDRESS, "k2"))
        self.assertEqual(len(a), 24)


class LogDigestTests(unittest.TestCase):
    """The label a caller wears in the log (and in the selection store) is keyed too,
    under a domain of its own."""

    ADDRESS = _identity(subject="203.0.113.42", source="ip", client="unknown")

    def test_it_is_keyed_by_the_secret_under_its_own_label(self):
        import hashlib
        import hmac

        with patch.object(anonymous, "SECRET", "k1"):
            got = anonymous.digest(self.ADDRESS)
        want = hmac.new(b"k1", b"pexafy-anon-log:v1:ip:203.0.113.42",
                        hashlib.sha256).hexdigest()[:12]
        self.assertEqual(got, want)
        self.assertNotEqual(got, hashlib.sha1(b"ip:203.0.113.42").hexdigest()[:12])
        # Not a prefix of the counter's name: the log cannot be joined to the API's
        # counter by string matching, and neither can be derived from the other.
        self.assertFalse(anonymous.subject_digest(self.ADDRESS, "k1").startswith(got))

    def test_another_secret_is_another_label(self):
        with patch.object(anonymous, "SECRET", "k1"):
            a = anonymous.digest(self.ADDRESS)
        with patch.object(anonymous, "SECRET", "k2"):
            b = anonymous.digest(self.ADDRESS)
        self.assertNotEqual(a, b)

    def test_without_a_secret_it_is_still_keyed(self):
        import hashlib

        with patch.object(anonymous, "SECRET", ""):
            got = anonymous.digest(self.ADDRESS)
        self.assertEqual(len(got), 12)
        self.assertNotEqual(got, hashlib.sha1(b"ip:203.0.113.42").hexdigest()[:12])

    def test_the_log_line_carries_the_keyed_label_and_never_the_address(self):
        import asyncio

        async def call_next(_context):
            return "answered"

        class _Ctx:
            message = None
            method = "tools/call"
            fastmcp_context = None

        with patch.object(anonymous, "ENABLED", True), \
             patch.object(anonymous, "SECRET", "k1"), \
             patch.object(anonymous, "_is_anonymous_request", lambda: True), \
             patch.object(anonymous, "_incoming_headers",
                          lambda: {"cf-connecting-ip": "203.0.113.42"}), \
             self.assertLogs("pexafy.mcp.anonymous", level="INFO") as logs:
            asyncio.run(anonymous.ResolveIdentity().on_message(_Ctx(), call_next))
        text = "\n".join(logs.output)
        self.assertNotIn("203.0.113.42", text)
        with patch.object(anonymous, "SECRET", "k1"):
            self.assertIn(f"id={anonymous.digest(self.ADDRESS)}", text)


class ClientIPTests(unittest.TestCase):
    def test_cloudflare_header_wins(self):
        self.assertEqual(
            anonymous.client_ip({"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "9.9.9.9"}),
            "1.2.3.4",
        )

    def test_forwarded_for_uses_the_leftmost_entry(self):
        """The leftmost entry is the original client; the ones to its right are the
        proxies it went through."""
        self.assertEqual(
            anonymous.client_ip({"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"}),
            "203.0.113.7",
        )

    def test_no_address_is_not_an_error(self):
        self.assertEqual(anonymous.client_ip({}), "")


class IPFeedTests(unittest.TestCase):
    def setUp(self):
        self.feed = anonymous._IPFeed("https://example.invalid/ips.json", ttl=3600)

    def _load(self, payload):
        async def handler(request):
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        import asyncio

        asyncio.run(self.feed.refresh(client))

    def test_a_bare_list_of_prefixes_loads(self):
        self._load(["203.0.113.0/24", "2001:db8::/32"])
        self.assertTrue(self.feed.contains("203.0.113.9"))
        self.assertTrue(self.feed.contains("2001:db8::1"))
        self.assertFalse(self.feed.contains("198.51.100.1"))

    def test_the_google_style_prefixes_object_loads(self):
        self._load({"prefixes": [{"ipv4Prefix": "203.0.113.0/24"}, {"ipv6Prefix": "2001:db8::/32"}]})
        self.assertTrue(self.feed.contains("203.0.113.1"))

    def test_unparseable_entries_are_skipped_not_fatal(self):
        self._load(["203.0.113.0/24", "not-an-ip", "", "999.999.999.999/8"])
        self.assertTrue(self.feed.contains("203.0.113.1"))

    def test_an_unreachable_feed_keeps_the_previous_list_and_warns(self):
        self._load(["203.0.113.0/24"])

        async def boom(request):
            raise httpx.ConnectError("down")

        import asyncio

        client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
        with self.assertLogs("pexafy.mcp.anonymous", level="WARNING"):
            asyncio.run(self.feed.refresh(client))
        self.assertTrue(self.feed.contains("203.0.113.1"), "the last good list survives")

    def test_an_empty_feed_does_not_erase_a_good_list(self):
        """An attestation list that parses to nothing would silently un-attest every
        caller; keeping the previous one is the safer failure."""
        self._load(["203.0.113.0/24"])
        with self.assertLogs("pexafy.mcp.anonymous", level="WARNING"):
            self._load([])
        self.assertTrue(self.feed.contains("203.0.113.1"))

    def test_a_feed_that_never_loaded_attests_nobody(self):
        self.assertFalse(self.feed.contains("203.0.113.1"))
        self.assertFalse(self.feed.loaded)

    def test_garbage_is_not_an_address(self):
        self._load(["203.0.113.0/24"])
        self.assertFalse(self.feed.contains("not-an-ip"))
        self.assertFalse(self.feed.contains(""))


@pytest.mark.asyncio
class TestResolve:
    """The source chain: first match wins, and no match means nobody."""

    async def test_openai_subject_from_the_body_wins(self):
        with patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]), \
             patch.object(anonymous, "ATTEST_OPENAI", False):
            got = await anonymous.resolve(
                {"cf-connecting-ip": "1.2.3.4"}, {"openai/subject": "sub_1"}, "ChatGPT/1.0"
            )
        assert got.source == "openai-subject"
        assert got.subject == "sub_1"

    async def test_the_header_is_read_when_the_body_has_nothing(self):
        with patch.object(anonymous, "SOURCES", ["openai-subject"]), \
             patch.object(anonymous, "ATTEST_OPENAI", False):
            got = await anonymous.resolve({"x-openai-subject": "sub_2"}, {}, "")
        assert got.subject == "sub_2"

    async def test_an_unattested_subject_is_still_returned_but_marked(self):
        """The decision to accept it belongs to the API's require_attested setting,
        not to a silent drop here.

        `_ever_loaded` matters as much as the networks: a feed that HAS loaded and does
        not contain the address is a verdict ("not attested"), while one that never
        loaded is an outage, handled separately in TestFeedOutage."""
        import ipaddress

        with patch.object(anonymous, "SOURCES", ["openai-subject"]), \
             patch.object(anonymous, "ATTEST_OPENAI", True), \
             patch.object(anonymous.openai_feed, "_networks",
                          [ipaddress.ip_network("198.51.100.0/24")]), \
             patch.object(anonymous.openai_feed, "_ever_loaded", True), \
             patch.object(anonymous.openai_feed, "_fetched_at", 1e18):
            got = await anonymous.resolve({"cf-connecting-ip": "9.9.9.9"}, {"openai/subject": "s"}, "")
        assert got is not None
        assert got.attested is False

    async def test_an_attested_subject_is_marked_attested(self):
        import ipaddress

        with patch.object(anonymous, "SOURCES", ["openai-subject"]), \
             patch.object(anonymous, "ATTEST_OPENAI", True), \
             patch.object(anonymous.openai_feed, "_networks",
                          [ipaddress.ip_network("203.0.113.0/24")]), \
             patch.object(anonymous.openai_feed, "_ever_loaded", True), \
             patch.object(anonymous.openai_feed, "_fetched_at", 1e18):
            got = await anonymous.resolve(
                {"cf-connecting-ip": "203.0.113.5"}, {"openai/subject": "s"}, ""
            )
        assert got.attested is True

    async def test_it_falls_through_to_the_address(self):
        with patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]):
            got = await anonymous.resolve({"cf-connecting-ip": "203.0.113.5"}, {}, "")
        assert got.source == "ip"
        assert got.subject == "203.0.113.5"

    async def test_the_session_source_is_opt_in(self):
        """Default configuration must not hand a budget to something a client can
        renew at will."""
        assert "mcp-session" not in anonymous.SOURCES
        with patch.object(anonymous, "SOURCES", ["mcp-session"]):
            got = await anonymous.resolve({"mcp-session-id": "abc123"}, {}, "")
        assert got.source == "mcp-session"
        assert got.subject == "abc123"

    async def test_nobody_identifiable_returns_none(self):
        with patch.object(anonymous, "SOURCES", ["openai-subject"]):
            assert await anonymous.resolve({}, {}, "") is None

    async def test_an_unknown_source_is_reported_not_ignored(self, caplog):
        """A typo in PEXAFY_ANON_SOURCES disables a source; silence would hide it."""
        with caplog.at_level("WARNING", logger="pexafy.mcp.anonymous"):
            with patch.object(anonymous, "SOURCES", ["typo-source"]):
                got = await anonymous.resolve({"cf-connecting-ip": "1.2.3.4"}, {}, "")
        assert got is None
        assert any("typo-source" in r.getMessage() for r in caplog.records)

    async def test_source_order_is_honoured(self):
        with patch.object(anonymous, "SOURCES", ["ip", "openai-subject"]), \
             patch.object(anonymous, "ATTEST_OPENAI", False):
            got = await anonymous.resolve(
                {"cf-connecting-ip": "1.2.3.4"}, {"openai/subject": "sub"}, ""
            )
        assert got.source == "ip", "the first configured source that matches must win"


class ConfigurationTests(unittest.TestCase):
    def test_disabled_is_not_configured(self):
        with patch.object(anonymous, "ENABLED", False), patch.object(anonymous, "SECRET", "s"):
            self.assertFalse(anonymous.is_configured())

    def test_enabled_without_a_secret_stays_off_and_warns(self):
        with patch.object(anonymous, "ENABLED", True), patch.object(anonymous, "SECRET", ""):
            with self.assertLogs("pexafy.mcp.anonymous", level="WARNING"):
                self.assertFalse(anonymous.is_configured())

    def test_enabled_with_a_secret_is_configured(self):
        with patch.object(anonymous, "ENABLED", True), patch.object(anonymous, "SECRET", "s"):
            self.assertTrue(anonymous.is_configured())

    def test_the_default_is_off(self):
        """Deploying this version must change nothing until someone turns it on."""
        self.assertNotIn("PEXAFY_ANON_ENABLED", os.environ)
        self.assertFalse(anonymous.ENABLED)


class SyntheticBearerTests(unittest.TestCase):
    def test_it_is_long_and_random(self):
        self.assertGreater(len(anonymous.SYNTHETIC_BEARER), 32)
        self.assertTrue(anonymous.SYNTHETIC_BEARER.startswith("anon:"))

    def test_only_the_exact_value_is_recognised(self):
        self.assertTrue(anonymous.is_synthetic_bearer(anonymous.SYNTHETIC_BEARER))
        for other in ("", "anon:", "anon:guess", anonymous.SYNTHETIC_BEARER + "x",
                      anonymous.SYNTHETIC_BEARER[:-1], "anon:é", "é" * 50):
            with self.subTest(token=other[:12]):
                self.assertFalse(anonymous.is_synthetic_bearer(other))


class AttachPrincipalTests(unittest.TestCase):
    class _Req:
        def __init__(self):
            self.headers = {}

    def test_no_identity_attaches_nothing(self):
        token = anonymous.current.set(None)
        try:
            request = self._Req()
            self.assertFalse(anonymous.attach_principal(request))
            self.assertEqual(request.headers, {})
        finally:
            anonymous.current.reset(token)

    def test_an_identity_is_signed_onto_the_request(self):
        token = anonymous.current.set(_identity())
        try:
            with patch.object(anonymous, "SECRET", GOLDEN_SECRET):
                request = self._Req()
                self.assertTrue(anonymous.attach_principal(request))
            self.assertIn(anonymous.PRINCIPAL_HEADER, request.headers)
        finally:
            anonymous.current.reset(token)

    def test_a_missing_secret_attaches_nothing_and_warns(self):
        token = anonymous.current.set(_identity())
        try:
            with patch.object(anonymous, "SECRET", ""):
                request = self._Req()
                with self.assertLogs("pexafy.mcp.anonymous", level="WARNING"):
                    self.assertFalse(anonymous.attach_principal(request))
            self.assertEqual(request.headers, {})
        finally:
            anonymous.current.reset(token)


class SharedNetworkTests(unittest.TestCase):
    """Claude.ai keeps OAuth: an address that stands for a crowd names nobody.

    Decided 2026-09-13. Every Claude.ai request leaves 160.79.104.0/21 and the host
    sends no identity, so `ip` there would be one budget for every Claude user alive.
    """

    def test_the_claude_range_is_shared_by_default(self):
        self.assertTrue(anonymous.is_shared_network("160.79.104.1"))
        self.assertTrue(anonymous.is_shared_network("160.79.111.255"))

    def test_an_ordinary_address_is_not(self):
        self.assertFalse(anonymous.is_shared_network("203.0.113.5"))
        self.assertFalse(anonymous.is_shared_network("160.79.112.1"), "just outside the /21")

    def test_garbage_is_not_a_shared_network(self):
        self.assertFalse(anonymous.is_shared_network("not-an-ip"))
        self.assertFalse(anonymous.is_shared_network(""))

    def test_the_list_is_configurable(self):
        import ipaddress

        with patch.object(anonymous, "_SHARED",
                          [ipaddress.ip_network("198.51.100.0/24")]):
            self.assertTrue(anonymous.is_shared_network("198.51.100.7"))
            self.assertFalse(anonymous.is_shared_network("160.79.104.1"))

    def test_an_unparseable_entry_is_reported_not_silently_dropped(self):
        with self.assertLogs("pexafy.mcp.anonymous", level="WARNING"):
            parsed = anonymous._parse_networks(["203.0.113.0/24", "nonsense"])
        self.assertEqual(len(parsed), 1)


@pytest.mark.asyncio
class TestSharedNetworkResolution:
    async def test_an_address_in_a_shared_network_resolves_to_nobody(self):
        with patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]):
            got = await anonymous.resolve({"cf-connecting-ip": "160.79.104.9"}, {}, "ClaudeAI/1.0")
        assert got is None, "Claude.ai must fall back to OAuth, not to a shared bucket"

    async def test_a_subject_from_a_shared_network_is_still_an_identity(self):
        """The network is only disqualified as an IDENTITY; a host that names the person
        is still believed (subject to attestation)."""
        with patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]), \
             patch.object(anonymous, "ATTEST_OPENAI", False):
            got = await anonymous.resolve(
                {"cf-connecting-ip": "160.79.104.9"}, {"openai/subject": "sub_x"}, ""
            )
        assert got is not None
        assert got.source == "openai-subject"


class AllowAnonymousGateTests(unittest.TestCase):
    """The ASGI gate decides on headers alone — all it has."""

    def _could(self, headers: dict) -> bool:
        raw = [(k.encode(), v.encode()) for k, v in headers.items()]
        return anonymous.AllowAnonymous._could_be_identified(raw)

    def test_an_ordinary_address_may_be_identified(self):
        self.assertTrue(self._could({"cf-connecting-ip": "203.0.113.5"}))

    def test_a_shared_network_with_nothing_else_may_not(self):
        self.assertFalse(self._could({"cf-connecting-ip": "160.79.104.9"}))

    def test_a_subject_header_is_enough_even_from_a_shared_network(self):
        self.assertTrue(self._could({"cf-connecting-ip": "160.79.104.9",
                                     "x-openai-subject": "sub_x"}))

    def test_no_address_at_all_may_not(self):
        self.assertFalse(self._could({}))

    def test_without_the_ip_source_the_body_gets_its_chance(self):
        """Resolution happens later with the body in hand; the gate must not pre-empt it."""
        with patch.object(anonymous, "SOURCES", ["openai-subject"]):
            self.assertTrue(self._could({"cf-connecting-ip": "160.79.104.9"}))


def _unreachable_feed():
    """The OpenAI feed during an outage: every refresh is refused in memory.

    The refresh itself is the real one, so its error handling is what gets exercised;
    only the transport under it fails. `asked` counts the attempts.
    """
    feed = anonymous._IPFeed("https://openai-feed.invalid/x.json", ttl=3600)
    feed.asked = 0
    refresh = feed.refresh

    async def refuse(request):
        raise httpx.ConnectError("feed unreachable", request=request)

    async def unreachable(client=None):
        feed.asked += 1
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as down:
            await refresh(down)

    feed.refresh = unreachable
    return feed


@pytest.mark.asyncio
class TestFeedOutage:
    """A feed that has never loaded must degrade, not blackout and not open the door.

    With `require_attested` on, calling an unknown address "not attested" would refuse
    every ChatGPT caller for as long as openai.com is unreachable from this host —
    someone else's outage becoming ours.
    """

    async def test_a_feed_that_never_loaded_gives_no_verdict(self):
        feed = _unreachable_feed()
        with patch.object(anonymous, "openai_feed", feed), \
             patch.object(anonymous, "ATTEST_OPENAI", True):
            # stale() is true, refresh() fails → never loaded.
            assert await anonymous.attest_openai("203.0.113.1") is None
        assert feed.asked == 1 and not feed.loaded

    async def test_a_loaded_feed_still_says_no_to_an_outside_address(self):
        import ipaddress

        feed = _unreachable_feed()
        feed._networks = [ipaddress.ip_network("198.51.100.0/24")]
        feed._ever_loaded = True
        feed._fetched_at = 1e18
        with patch.object(anonymous, "openai_feed", feed), \
             patch.object(anonymous, "ATTEST_OPENAI", True):
            assert await anonymous.attest_openai("203.0.113.1") is False
            assert await anonymous.attest_openai("198.51.100.7") is True
        assert feed.asked == 0, "a fresh list is not fetched again"

    async def test_attestation_off_answers_yes_without_consulting_anything(self):
        feed = _unreachable_feed()
        with patch.object(anonymous, "openai_feed", feed), \
             patch.object(anonymous, "ATTEST_OPENAI", False):
            assert await anonymous.attest_openai("anything") is True
        assert feed.asked == 0

    async def test_an_outage_falls_through_to_the_address(self):
        """The degradation: a budget shared by address rather than none at all."""
        feed = _unreachable_feed()
        with patch.object(anonymous, "openai_feed", feed), \
             patch.object(anonymous, "ATTEST_OPENAI", True), \
             patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]):
            got = await anonymous.resolve(
                {"cf-connecting-ip": "203.0.113.9"}, {"openai/subject": "sub_x"}, ""
            )
        assert feed.asked == 1
        assert got is not None, "an outage must not leave the caller unidentifiable"
        assert got.source == "ip"
        assert got.subject == "203.0.113.9"

    async def test_an_outage_is_said_out_loud(self, caplog):
        feed = _unreachable_feed()
        with caplog.at_level("WARNING", logger="pexafy.mcp.anonymous"):
            with patch.object(anonymous, "openai_feed", feed), \
                 patch.object(anonymous, "ATTEST_OPENAI", True), \
                 patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]):
                await anonymous.resolve(
                    {"cf-connecting-ip": "203.0.113.9"}, {"openai/subject": "s"}, ""
                )
        messages = [r.getMessage() for r in caplog.records]
        assert any("Could not refresh the IP list" in m for m in messages), messages
        assert any("never loaded" in m for m in messages), messages

    async def test_with_no_fallback_source_an_outage_identifies_nobody(self):
        """Rather than hand out an unattested identity the caller cannot be held to."""
        feed = _unreachable_feed()
        with patch.object(anonymous, "openai_feed", feed), \
             patch.object(anonymous, "ATTEST_OPENAI", True), \
             patch.object(anonymous, "SOURCES", ["openai-subject"]):
            assert await anonymous.resolve(
                {"cf-connecting-ip": "203.0.113.9"}, {"openai/subject": "s"}, ""
            ) is None
        assert feed.asked == 1

    async def test_a_shared_network_plus_an_outage_still_identifies_nobody(self):
        """Claude.ai with the feed down: still the 401, not someone else's budget."""
        feed = _unreachable_feed()
        with patch.object(anonymous, "openai_feed", feed), \
             patch.object(anonymous, "ATTEST_OPENAI", True), \
             patch.object(anonymous, "SOURCES", ["openai-subject", "ip"]):
            assert await anonymous.resolve(
                {"cf-connecting-ip": "160.79.104.9"}, {"openai/subject": "s"}, ""
            ) is None
        assert feed.asked == 1


if __name__ == "__main__":
    unittest.main()
