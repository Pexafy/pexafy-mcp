"""The grid's coach, once per person.

The coach — three first steps under the grid, the tile that lifts, the heart that
blinks, the card that sways in the deck — kept its progress with one answer: the frame
can store nothing past it (a sandbox with an opaque origin), so it came back in every
conversation, for everybody. It now plays once per person, ever. The first grid that
shows it tells the server (POST /coach, with the signed token of `meta_for_result`),
and every answer after that carries `show: false`.

A person is, in this order: the anonymous principal the caller is served as
(anonymous.digest — a ChatGPT installation, or an address); the account behind an OAuth
token (a keyed digest of its e-mail, the same across its clients, devices and tokens);
the API key a caller brought (its digest). Nothing is kept but that digest and the fact.

    PEXAFY_COACH_ONCE  1   the coach plays once per person; 0 = in every grid, as before
"""
from __future__ import annotations

import hashlib
import logging

from . import anonymous, auth, observe, selection, store
from .env import flag

logger = logging.getLogger("pexafy.mcp.coach")

META_KEY = "pexafy/coach"
ONCE = flag("PEXAFY_COACH_ONCE", True)
# Long enough to be "ever" for a product, short enough not to be kept forever.
TTL = 400 * 24 * 3600


def _access_token():
    try:
        from fastmcp.server.dependencies import get_access_token

        return get_access_token()
    except Exception:  # noqa: BLE001 — no request in scope (a unit call, stdio)
        return None


def person_key() -> str:
    """A stable, opaque name for the person behind the current call, or ""."""
    identity = anonymous.current.get()
    if identity is not None:
        return "a:" + anonymous.digest(identity)
    token = _access_token()
    if token is None:
        return ""
    claims = token.claims or {}
    if claims.get(auth.ANONYMOUS_CLAIM):
        return ""               # served anonymously, but nobody could be named
    email = claims.get("email") or token.subject
    if email:
        return "u:" + observe.email_digest(email)
    key = claims.get(auth.API_KEY_CLAIM)
    if key:
        return "k:" + hashlib.sha256(f"coach:{key}".encode()).hexdigest()[:24]
    return ""


async def met(person: str) -> bool:
    return bool(person) and await store.has("coach:" + person)


async def mark(person: str) -> None:
    if person:
        await store.put("coach:" + person, TTL)


async def meta_for_result() -> dict | None:
    """For an answer the grid draws: `{"show": False}` for somebody who has met the coach;
    for somebody who has not, `show: True` with where and how to say they just did.
    None when the feature is off or nobody can be named: the grid coaches, as before."""
    if not ONCE:
        return None
    person = person_key()
    if not person:
        return None
    if await met(person):
        return {"show": False}
    token = selection.issue_token(person)
    if not (token and selection.PUBLIC_URL):
        return {"show": True}
    return {"show": True, "post_url": selection.PUBLIC_URL + "/coach", "token": token}
