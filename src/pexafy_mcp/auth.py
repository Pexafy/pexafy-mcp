"""OAuth Resource-Server token verification for the Pexafy MCP server.

django-oauth-toolkit issues OPAQUE access tokens (not JWTs), so they cannot be
verified locally with a public key. Instead we delegate to Django's internal
`/oauth/mcp/resolve` endpoint, which in a single call BOTH:
  - validates the bearer token against the DOT token store, and
  - returns the user's Pexafy API key (minted lazily, persisted encrypted).

The resolved API key is stashed in the AccessToken `claims` so the outbound HTTP
hook in server.py can send it as `x-api-key` when calling the Pexafy API.
"""
from __future__ import annotations

import logging

import httpx
from fastmcp.server.auth import AccessToken, TokenVerifier

from . import limits

logger = logging.getLogger(__name__)

# Claim key under which the resolved Pexafy API key is carried.
API_KEY_CLAIM = "pexafy_api_key"
# Claim carrying a sales message when the user is blocked by a plan limit (no key).
QUOTA_MESSAGE_CLAIM = "pexafy_quota_message"


class PexafyResolveVerifier(TokenVerifier):
    """Validate an OAuth token by resolving it (server-side) to a Pexafy API key."""

    def __init__(self, resolve_url: str, resolve_secret: str,
                 required_scopes: list[str] | None = None,
                 advertised_scopes: list[str] | None = None):
        super().__init__(required_scopes=required_scopes)
        self._url = resolve_url
        self._secret = resolve_secret
        # Search-only connector: advertise/grant `read` alone (no collection writes).
        self._advertised_scopes = advertised_scopes or ["read"]
        self._client = httpx.AsyncClient(timeout=10)

    @property
    def scopes_supported(self) -> list[str]:
        # Advertise the OAuth scopes the AS grants (read/write), independent of
        # required_scopes (which gate every request — kept minimal/None here).
        return self._advertised_scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        # Dual-mode: a raw Pexafy API key presented as the bearer is accepted
        # directly (per-user key auth, no OAuth). The Pexafy API validates the key
        # on the actual call — an invalid key simply yields a 401 from the API — so
        # there is nothing to verify here; we just forward it as x-api-key.
        if token.startswith("pexafy_"):
            logger.debug("Auth: direct raw Pexafy key presented as bearer")
            return AccessToken(
                token=token,
                client_id="pexafy-direct-key",
                scopes=self._advertised_scopes,
                claims={API_KEY_CLAIM: token},
            )

        # Otherwise it is an opaque OAuth (DOT) token — resolve it server-side.
        try:
            resp = await self._client.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-MCP-Resolve-Secret": self._secret,
                    # Internal call is plain HTTP (pexafy_net); tell Django it is
                    # secure so SECURE_SSL_REDIRECT doesn't 301 the POST away.
                    "X-Forwarded-Proto": "https",
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("Token resolution request failed: %s", exc)
            return None

        if resp.status_code != 200:
            # Plan-limit denial (the user's token is valid, but no API key could be
            # minted because their plan's key limit is reached): let the client
            # connect and carry a sales message so every tool call explains the
            # upgrade path in-chat instead of a generic auth failure.
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {}
            if resp.status_code == 403 and data.get("error") == "key_provisioning_denied":
                logger.info("Token valid but key blocked by plan limit (%s)", data.get("email") or "?")
                msg = limits.key_limit_message(data.get("context") or {})
                return AccessToken(
                    token=token,
                    client_id=data.get("email") or "pexafy-quota",
                    scopes=self._advertised_scopes,
                    claims={QUOTA_MESSAGE_CLAIM: msg},
                )
            # 401 invalid token, 403 bad secret — genuinely not authorized.
            logger.info("Token resolution rejected: %s", resp.status_code)
            return None

        data = resp.json()
        api_key = data.get("api_key")
        if not api_key:
            logger.warning("Token resolved 200 but no api_key in payload")
            return None
        logger.info("OAuth token resolved to API key for %s", data.get("email") or "?")

        scope = data.get("scope") or ""
        scopes = scope.split() if isinstance(scope, str) else list(scope)
        return AccessToken(
            token=token,
            client_id=data.get("email") or "pexafy-mcp",
            scopes=scopes,
            subject=data.get("email"),
            claims={API_KEY_CLAIM: api_key, "email": data.get("email")},
        )
