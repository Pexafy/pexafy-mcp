"""OAuth Resource-Server token verification for the Pexafy MCP server.

django-oauth-toolkit issues OPAQUE access tokens (not JWTs), so they cannot be
verified locally with a public key. Verification is delegated to Django's internal
`/oauth/mcp/resolve` endpoint, which in a single call BOTH:
  - validates the bearer token against the DOT token store, and
  - returns the user's Pexafy API key (minted lazily, persisted encrypted).

The resolved API key is stashed in the AccessToken `claims` so the outbound HTTP
hook in server.py can send it as `x-api-key` when calling the Pexafy API.

Three other bearers are recognised first, without asking Django: the synthetic bearer
AllowAnonymous gives a credential-less request (anonymous.py), a Pexafy API key
presented as the bearer, and the monitoring token that /metrics checks itself.
"""
from __future__ import annotations

import logging
import secrets

import httpx
from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier

from . import anonymous, limits, observe

logger = logging.getLogger(__name__)

# Claim key under which the resolved Pexafy API key is carried.
API_KEY_CLAIM = "pexafy_api_key"
# Claim carrying the plan-limit message when no key could be minted for the user.
QUOTA_MESSAGE_CLAIM = "pexafy_quota_message"
# Claim marking a caller who presented no credential of their own.
ANONYMOUS_CLAIM = "pexafy_anonymous"


class PexafyResolveVerifier(TokenVerifier):
    """Validate an OAuth token by resolving it (server-side) to a Pexafy API key."""

    def __init__(self, resolve_url: str, resolve_secret: str,
                 required_scopes: list[str] | None = None,
                 advertised_scopes: list[str] | None = None,
                 not_oauth_tokens: list[str] | None = None,
                 client: httpx.AsyncClient | None = None):
        super().__init__(required_scopes=required_scopes)
        self._url = resolve_url
        self._secret = resolve_secret
        # Search-only connector: advertise/grant `read` alone (no collection writes).
        self._advertised_scopes = advertised_scopes or ["read"]
        # Bearers this server knows by other means and must not send to Django as
        # OAuth tokens — today the monitoring token used by /metrics. Left in, a
        # scrape every few seconds resolves it every time and Django logs a rejection
        # for each one, burying real token failures in the same log line.
        self._not_oauth = [t for t in (not_oauth_tokens or []) if t]
        self._client = client or httpx.AsyncClient(timeout=10)

    @property
    def scopes_supported(self) -> list[str]:
        # What the protected-resource metadata advertises: `read` alone, the one scope
        # this connector is granted. Independent of required_scopes, which would gate
        # every request and are left unset.
        return self._advertised_scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        # Every credential accepted here is ASCII — a DOT token, a Pexafy key, the
        # synthetic bearer. Anything else is refused before it reaches a comparison, or
        # an outgoing header, that cannot hold it: a 401, not a 500.
        if not token.isascii():
            return None

        # The synthetic bearer this process gave a credential-less request, so FastMCP's
        # auth middleware lets it through (see anonymous.AllowAnonymous). It carries no
        # API key: the outgoing request falls back to the service key, and WHO is asking
        # travels separately as a signed principal. The value is a per-process nonce, so
        # a client cannot present it.
        if anonymous.is_synthetic_bearer(token):
            return AccessToken(
                token=token,
                client_id="pexafy-anonymous",
                scopes=["read"],
                claims={ANONYMOUS_CLAIM: True},
            )

        # Dual-mode: a raw Pexafy API key presented as the bearer is accepted
        # directly (per-user key auth, no OAuth). The Pexafy API validates the key
        # on the actual call — an invalid key simply yields a 401 from the API — so
        # there is nothing to verify here; it is forwarded as x-api-key.
        if token.startswith("pexafy_"):
            logger.debug("Auth: direct raw Pexafy key presented as bearer")
            return AccessToken(
                token=token,
                client_id="pexafy-direct-key",
                scopes=self._advertised_scopes,
                claims={API_KEY_CLAIM: token},
            )

        # A credential meant for another route (the metrics token) is not an
        # OAuth token: the route checks it itself, and resolving it here would
        # only make Django log a rejection. Constant-time, it is a secret.
        presented = token.encode()
        if any(secrets.compare_digest(presented, known.encode()) for known in self._not_oauth):
            return None

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

        # A body that is not a JSON object (a proxy's HTML error page, say) carries
        # nothing usable: read as empty, it falls through to a refusal below.
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {}

        if resp.status_code != 200:
            # Plan-limit denial: the token is valid, but no API key could be minted
            # because the plan's key limit is reached. The client is let in carrying
            # the limit message (limits.key_limit_message), so every tool call says in
            # chat why it cannot search instead of failing as an auth error.
            if resp.status_code == 403 and data.get("error") == "key_provisioning_denied":
                logger.info("Token valid but key blocked by plan limit (user %s)",
                            _user(data.get("email")))
                context = data.get("context")
                msg = limits.key_limit_message(context if isinstance(context, dict) else {})
                return AccessToken(
                    token=token,
                    client_id=data.get("email") or "pexafy-quota",
                    scopes=self._advertised_scopes,
                    claims={QUOTA_MESSAGE_CLAIM: msg},
                )
            # 401 invalid token, 403 bad secret — genuinely not authorized.
            logger.info("Token resolution rejected: %s", resp.status_code)
            return None

        api_key = data.get("api_key")
        if not api_key:
            logger.warning("Token resolved 200 but no api_key in payload")
            return None
        logger.info("OAuth token resolved to API key for user %s", _user(data.get("email")))

        scope = data.get("scope") or ""
        scopes = scope.split() if isinstance(scope, str) else list(scope)
        return AccessToken(
            token=token,
            client_id=data.get("email") or "pexafy-mcp",
            scopes=scopes,
            subject=data.get("email"),
            claims={API_KEY_CLAIM: api_key, "email": data.get("email")},
        )


def _user(email) -> str:
    """Who, in a log line: a keyed digest of the e-mail, never the address nor a plain
    hash of it (observe.email_digest). These lines are written on every OAuth request;
    the digest still tells one user from another."""
    return observe.email_digest(email)


class RootAliasedAuthProvider(RemoteAuthProvider):
    """Serve the protected-resource metadata at the well-known root as well.

    RFC 9728 §3.1 puts the document at `/.well-known/oauth-protected-resource`
    followed by the resource's own path — here,
    `/.well-known/oauth-protected-resource/mcp`. That is what FastMCP registers, and
    it is correct.

    Clients do not all agree: several probe the bare
    `/.well-known/oauth-protected-resource`, and a 404 there is read as "this server
    has no authentication" even when the 401 carries a `WWW-Authenticate` header
    pointing at the real document.

    So the same handler is mounted at the root path too. Not a second document: the
    route object generated by the parent is reused, so the two cannot drift apart.
    """
    _ROOT_PATH = "/.well-known/oauth-protected-resource"

    def get_routes(self, mcp_path: str | None = None) -> list:
        routes = super().get_routes(mcp_path)
        if not routes or any(r.path == self._ROOT_PATH for r in routes):
            return routes
        from starlette.routing import Route

        canonical = routes[0]
        routes.append(
            Route(
                self._ROOT_PATH,
                endpoint=canonical.endpoint,
                methods=["GET", "OPTIONS"],
                name="oauth_protected_resource_root_alias",
            )
        )
        return routes
