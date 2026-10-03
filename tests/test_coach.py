"""The coach plays once per person (coach.py), and the store that remembers it (store.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastmcp.server.auth import AccessToken

from pexafy_mcp import anonymous, auth, coach, observe, selection, store


@pytest.fixture(autouse=True)
def _clean_store():
    with patch.object(store, "URL", ""):
        store._memory.clear()
        yield
        store._memory.clear()


def _run(coro):
    return asyncio.run(coro)


# ── The store ────────────────────────────────────────────────────────────────

def test_the_store_holds_renews_and_forgets():
    assert _run(store.has("x")) is False
    _run(store.put("x", 60))
    assert _run(store.has("x")) is True
    store._memory[store.PREFIX + "x"] = 0                 # expired
    assert _run(store.has("x")) is False
    _run(store.put("y", 60))
    _run(store.drop("y"))
    assert _run(store.has("y")) is False


def test_a_failing_redis_never_fails_a_request(caplog):
    class Broken:
        async def exists(self, *a):
            raise ConnectionError("down")

        async def set(self, *a, **k):
            raise ConnectionError("down")

        async def delete(self, *a):
            raise ConnectionError("down")

    with patch.object(store, "URL", "redis://nowhere:6379/2"), \
         patch.object(store, "_client", Broken()):
        assert _run(store.has("x")) is False
        _run(store.put("x", 60))
        _run(store.drop("x"))
    assert "Store read failed" in caplog.text and "Store write failed" in caplog.text


# ── Who the person is ────────────────────────────────────────────────────────

def _token(**claims):
    return AccessToken(token="t", client_id="c", scopes=[], subject=claims.get("email"),
                       claims=claims)


def test_an_account_is_one_person_across_its_tokens():
    email = "someone@example.com"
    with patch.object(coach, "_access_token", lambda: _token(email=email)):
        first = coach.person_key()
    with patch.object(coach, "_access_token",
                      lambda: AccessToken(token="other", client_id="d", scopes=[],
                                          subject=email, claims={"email": email})):
        second = coach.person_key()
    assert first == second == "u:" + observe.email_digest(email)
    assert email not in first


def test_an_anonymous_caller_is_their_principal():
    identity = anonymous.Identity(source="ip", subject="203.0.113.9", attested=True,
                                  client="Visual Studio Code")
    token = anonymous.current.set(identity)
    try:
        assert coach.person_key() == "a:" + anonymous.digest(identity)
    finally:
        anonymous.current.reset(token)


def test_a_raw_key_is_its_digest_and_nobody_is_nothing():
    with patch.object(coach, "_access_token", lambda: _token(**{auth.API_KEY_CLAIM: "pexafy_k"})):
        key = coach.person_key()
    assert key.startswith("k:") and "pexafy_k" not in key
    with patch.object(coach, "_access_token", lambda: _token(**{auth.ANONYMOUS_CLAIM: True})):
        assert coach.person_key() == ""
    with patch.object(coach, "_access_token", lambda: None):
        assert coach.person_key() == ""


# ── Once ─────────────────────────────────────────────────────────────────────

def _meta_for(person: str):
    with patch.object(coach, "person_key", lambda: person), \
         patch.object(selection, "PUBLIC_URL", "https://mcp.example"):
        return _run(coach.meta_for_result())


def test_the_first_grid_coaches_and_says_where_to_report():
    meta = _meta_for("u:abc")
    assert meta["show"] is True
    assert meta["post_url"] == "https://mcp.example/coach"
    assert selection.read_token(meta["token"]) == "u:abc"


def test_once_shown_never_again():
    _run(coach.mark("u:abc"))
    assert _meta_for("u:abc") == {"show": False}
    assert _meta_for("u:someone-else")["show"] is True


def test_off_or_nobody_leaves_the_grid_as_before():
    assert _meta_for("") is None
    with patch.object(coach, "ONCE", False):
        assert _meta_for("u:abc") is None


# ── The report ───────────────────────────────────────────────────────────────

def test_the_grid_reports_through_a_signed_token():
    from starlette.testclient import TestClient

    from pexafy_mcp import server

    with patch.object(selection, "PUBLIC_URL", "https://mcp.example"):
        token = selection.issue_token("u:abc")
    with TestClient(server.build_server().http_app()) as client:
        pre = client.options("/coach")
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == "*"
        bad = client.post("/coach", content='{"token": "forged"}',
                          headers={"content-type": "text/plain"})
        assert bad.status_code == 401
        assert _run(coach.met("u:abc")) is False
        ok = client.post("/coach", content='{"token": "%s"}' % token,
                         headers={"content-type": "text/plain"})
        assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert _run(coach.met("u:abc")) is True
