"""A small durable memory: what has to outlive one answer, and one process.

Two facts are kept, each about a person or a machine, never about a search:

  * that somebody has met the grid's coach (coach.py) — it plays once per person;
  * that an editor on a machine has signed in (anonymous.py) — it is asked to sign in
    at every start from then on, so the account stays attached.

In Redis when PEXAFY_REDIS_URL names one — the platform's, which writes to disk —
otherwise in this process's memory, which forgets on a restart and says so once. Keys
are digests, never an address, an e-mail or a key. A store that fails never fails a
request: a read answers "not known", a write is dropped, each with a warning.

    PEXAFY_REDIS_URL   (empty)   redis://[:password@]host:port/db
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("pexafy.mcp.store")

URL = os.environ.get("PEXAFY_REDIS_URL", "").strip()
PREFIX = "pexafy:mcp:"
# A request waits this long for Redis at most: a slow store costs a coach or a sign-in
# prompt, never a search.
TIMEOUT_S = 0.5

_memory: dict[str, float] = {}      # full key -> expiry (epoch seconds)
_client = None
_said_memory = False


def _redis():
    """The Redis client, made on first use, or None when no URL is configured."""
    global _client
    if not URL:
        return None
    if _client is None:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(URL, socket_timeout=TIMEOUT_S,
                                    socket_connect_timeout=TIMEOUT_S)
    return _client


def _in_memory() -> None:
    global _said_memory
    if not _said_memory:
        _said_memory = True
        logger.warning("PEXAFY_REDIS_URL is not set: the coach and editor sign-ins are "
                       "remembered in this process only, and forgotten on a restart")


async def has(key: str) -> bool:
    """True when `key` is held and not expired."""
    full = PREFIX + key
    client = _redis()
    if client is None:
        _in_memory()
        expiry = _memory.get(full)
        if expiry is None:
            return False
        if expiry < time.time():
            _memory.pop(full, None)
            return False
        return True
    try:
        return bool(await client.exists(full))
    except Exception as exc:  # noqa: BLE001 — a store must not fail a request
        logger.warning("Store read failed (%s: %s): taken as not known",
                       type(exc).__name__, exc)
        return False


async def put(key: str, ttl: int) -> None:
    """Hold `key` for `ttl` seconds, from now (a put renews it)."""
    full = PREFIX + key
    client = _redis()
    if client is None:
        _in_memory()
        _memory[full] = time.time() + ttl
        return
    try:
        await client.set(full, b"1", ex=ttl)
    except Exception as exc:  # noqa: BLE001 — a store must not fail a request
        logger.warning("Store write failed (%s: %s): dropped", type(exc).__name__, exc)


async def drop(key: str) -> None:
    """Forget `key`."""
    full = PREFIX + key
    client = _redis()
    if client is None:
        _memory.pop(full, None)
        return
    try:
        await client.delete(full)
    except Exception as exc:  # noqa: BLE001 — a store must not fail a request
        logger.warning("Store delete failed (%s: %s): dropped", type(exc).__name__, exc)
