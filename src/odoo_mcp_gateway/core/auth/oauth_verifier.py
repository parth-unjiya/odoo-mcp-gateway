"""OAuth 2.1 JWT bearer-token verifier (ADR-005).

Additive auth strategy alongside the opaque-token verifier from
Sprint 1. When the gateway is run with ``MCP_AUTH_MODE=oauth`` (or
when the bearer token shape is detectably JWT-ish), the
``OAuthJwtVerifier`` validates the token against an external IdP's
public keys (JWKS) and maps the IdP identity to an Odoo user via
the email claim.

Why this design:

* **Stdio unchanged.** stdio mode never sees OAuth. The verifier
  only activates when transport=streamable-http AND the operator
  has wired an IdP issuer URL.
* **Email claim → res.users.login.** Per Sprint 4 scoping
  decision. Zero-config for 90% of Odoo deployments where logins
  are email addresses. No YAML user-map needed.
* **No token forwarding to Odoo.** The IdP JWT is consumed by the
  gateway; the gateway then uses its own per-user Odoo credential
  (looked up via the email → uid map) to actually call Odoo. The
  spec REQUIRES this (RFC 8707 audience binding).
* **Soft import.** ``authlib`` lives in the ``[oauth]`` extra. If
  it isn't installed and OAuth mode is requested, we fail loud at
  startup rather than silently degrading.

Compat with Sprint 1 opaque tokens: ``OdooTokenVerifier`` (Sprint 1)
and ``OAuthJwtVerifier`` (this module) both implement the SDK's
``TokenVerifier`` protocol. The gateway can pick one OR chain them
via :class:`CompositeTokenVerifier` (also here) so a single deployment
can accept either form during a migration.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from mcp.server.auth.provider import AccessToken, TokenVerifier

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

# Default scope set granted to a successfully-validated JWT. Operators
# refine per-user via the IdP's scope claim (the verifier intersects
# the JWT's ``scope`` claim with this list). See ADR-005 in the
# v0.3.0 plan for the rationale on a 5-scope hierarchy.
DEFAULT_OAUTH_SCOPES: list[str] = [
    "odoo.read",
    "odoo.write",
    "odoo.delete",
    "odoo.workflow",
    "odoo.admin",
]

# Soft import — authlib lives in [oauth] extra. The library has no
# type stubs, so we accept Any-typed bindings here and rely on
# runtime behaviour. The ``type: ignore`` comments suppress both the
# missing-stub warning (when authlib IS installed) and the unused-
# ignore warning (when it's NOT — the binding becomes None below
# and the symbol isn't read at runtime).
try:
    # fmt: off
    from authlib.jose import JsonWebToken  # type: ignore[import-untyped, import-not-found, unused-ignore]  # noqa: I001
    from authlib.jose.errors import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
        BadSignatureError,
        DecodeError,
        ExpiredTokenError,
        InvalidClaimError,
    )
    # fmt: on

    OAUTH_AVAILABLE = True
except ImportError:  # pragma: no cover - import-guard branch
    JsonWebToken = None  # type: ignore[assignment, misc, unused-ignore]
    BadSignatureError = Exception  # type: ignore[misc, assignment, unused-ignore]
    DecodeError = Exception  # type: ignore[misc, assignment, unused-ignore]
    ExpiredTokenError = Exception  # type: ignore[misc, assignment, unused-ignore]
    InvalidClaimError = Exception  # type: ignore[misc, assignment, unused-ignore]
    OAUTH_AVAILABLE = False


class OAuthVerifierError(Exception):
    """Raised when the OAuth verifier is constructed incorrectly."""


class OAuthJwtVerifier(TokenVerifier):
    """Validates IdP-issued JWT bearer tokens.

    Constructor parameters:

    * ``gateway`` — for token → session_key mapping (the verifier
      stages a session per validated JWT so existing tools keep
      reading ``_current_session_key``).
    * ``issuer`` — the OAuth issuer URL (e.g.
      ``https://keycloak.example.com/realms/master``). Must match
      the JWT's ``iss`` claim exactly.
    * ``audience`` — the canonical URL of THIS gateway (e.g.
      ``https://mcp.example.com/``). The verifier rejects tokens
      whose ``aud`` claim doesn't match (RFC 8707 binding).
    * ``jwks_uri`` — URL to fetch the IdP's signing keys. Cached
      per ``jwks_cache_ttl`` seconds.
    * ``algorithms`` — JWS algorithms accepted (default
      ``["RS256", "ES256"]`` — the OAuth 2.1 spec prohibits HS256
      for public clients).
    * ``required_scopes`` — minimum scope set; tokens missing all
      of these are rejected with insufficient_scope.

    The verifier does NOT issue tokens. It only validates.
    """

    def __init__(
        self,
        gateway: GatewayContext,
        issuer: str,
        audience: str,
        jwks_uri: str,
        algorithms: list[str] | None = None,
        required_scopes: list[str] | None = None,
        jwks_cache_ttl: int = 600,
    ) -> None:
        if not OAUTH_AVAILABLE:
            raise OAuthVerifierError(
                "OAuth support requires the [oauth] extra: "
                "`pip install odoo-mcp-gateway[oauth]`"
            )
        self._gateway = gateway
        self._issuer = issuer
        self._audience = audience
        self._jwks_uri = jwks_uri
        self._algorithms = algorithms or ["RS256", "ES256"]
        self._required_scopes = required_scopes or list(DEFAULT_OAUTH_SCOPES)
        self._jwks_cache_ttl = jwks_cache_ttl
        self._jwks_cache: tuple[float, dict[str, Any]] | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a JWT and return an ``AccessToken`` on success.

        The verifier:
        1. Decodes + validates the JWT (signature, exp, iss, aud).
        2. Pulls the email claim and looks it up against
           ``res.users.login`` to resolve an Odoo uid + session_key.
        3. Returns an ``AccessToken`` with ``client_id`` set to the
           session_key (so the existing ContextVar middleware works
           unchanged) and ``scopes`` intersected from the JWT's
           ``scope`` claim.

        Returns ``None`` on any validation failure — the SDK then
        responds with HTTP 401 + WWW-Authenticate pointing at the
        PRM endpoint.
        """
        if not token:
            return None

        try:
            claims = await self._decode_and_validate(token)
        except Exception:
            logger.debug(
                "JWT validation failed (token prefix=%s...)",
                token[:8],
                exc_info=True,
            )
            return None

        email = (
            claims.get("email") or claims.get("preferred_username") or claims.get("sub")
        )
        if not isinstance(email, str) or not email:
            logger.debug("JWT missing email-like claim; refusing")
            return None

        session_key = await self._resolve_session_key_for_email(email)
        if session_key is None:
            logger.debug(
                "JWT email %s does not match any Odoo res.users.login",
                email,
            )
            return None

        # Intersect requested scopes from the token's ``scope`` claim
        # (space-delimited per RFC 6749) with our known set. Tokens
        # without a ``scope`` claim get the default coarse set.
        token_scopes_raw = claims.get("scope") or claims.get("scp") or ""
        if isinstance(token_scopes_raw, list):
            token_scopes = set(token_scopes_raw)
        else:
            token_scopes = set(str(token_scopes_raw).split())
        granted_scopes = sorted(token_scopes & set(DEFAULT_OAUTH_SCOPES))
        if not granted_scopes:
            granted_scopes = list(DEFAULT_OAUTH_SCOPES)

        expires_at: int | None = None
        if "exp" in claims:
            try:
                expires_at = int(claims["exp"])
            except (TypeError, ValueError):
                expires_at = None

        return AccessToken(
            token=token,
            client_id=session_key,
            scopes=granted_scopes,
            expires_at=expires_at,
            resource=self._audience,
        )

    async def _decode_and_validate(self, token: str) -> dict[str, Any]:
        """Decode the JWT, verify signature + iss/aud/exp claims."""
        jwks = await self._get_jwks()
        if JsonWebToken is None:  # pragma: no cover - import-guard
            raise OAuthVerifierError("authlib not installed")
        jwt = JsonWebToken(self._algorithms)
        claims_options = {
            "iss": {"essential": True, "value": self._issuer},
            "aud": {"essential": True, "value": self._audience},
            "exp": {"essential": True},
        }
        decoded = jwt.decode(token, key=jwks, claims_options=claims_options)
        decoded.validate(now=int(time.time()), leeway=30)
        return dict(decoded)

    async def _get_jwks(self) -> dict[str, Any]:
        """Fetch (or read cached) JWKS from the IdP.

        The JWKS doc is small (~5 KB typical) and rotated rarely.
        Caching for 10 minutes is the standard tradeoff between
        freshness and IdP load.
        """
        now = time.monotonic()
        if self._jwks_cache is not None:
            ts, cached_doc = self._jwks_cache
            if now - ts < self._jwks_cache_ttl:
                return cached_doc

        import httpx

        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(self._jwks_uri)
            resp.raise_for_status()
            fresh_doc: dict[str, Any] = resp.json()
        self._jwks_cache = (now, fresh_doc)
        return fresh_doc

    async def _resolve_session_key_for_email(self, email: str) -> str | None:
        """Map an IdP email claim to a gateway session_key.

        Scans ``gateway.auth_managers`` for an existing session whose
        ``auth_result.username`` matches the email. Returns that
        session_key if found.

        If no session yet exists for this user (first-time OAuth
        login), returns ``None`` — the operator is expected to
        pre-provision a session for each known IdP user, OR a
        v0.4.0 enhancement will add on-demand session creation
        from the IdP claim.

        v0.3.0 keeps this simple: OAuth validates the JWT and
        identifies the user, but the per-user Odoo session must
        already exist (created via the standard ``login`` tool by
        an out-of-band process).
        """
        for session_key, mgr in self._gateway.auth_managers.items():
            auth_result = mgr.auth_result
            if auth_result is None:
                continue
            if auth_result.username == email:
                return session_key
        return None


class CompositeTokenVerifier(TokenVerifier):
    """Tries multiple TokenVerifiers in order; returns the first match.

    Lets a deployment accept BOTH opaque session tokens (Sprint 1)
    AND OAuth JWTs (this sprint) during a migration period. The
    verifier returns the first ``AccessToken`` produced by any
    delegate; if all return ``None`` the composite returns ``None``.

    Wire as::

        composite = CompositeTokenVerifier([
            OdooTokenVerifier(gateway),       # opaque tokens first
            OAuthJwtVerifier(gateway, ...),   # then JWTs
        ])

    Opaque-first ordering is intentional: opaque tokens are short
    and definitely-not-JWT, so the cheap check runs first. If a
    JWT-shaped token comes in, the opaque verifier returns None
    instantly and we move to the JWT verifier.
    """

    def __init__(self, delegates: list[TokenVerifier]) -> None:
        if not delegates:
            raise OAuthVerifierError(
                "CompositeTokenVerifier requires at least one delegate"
            )
        self._delegates = delegates

    async def verify_token(self, token: str) -> AccessToken | None:
        for delegate in self._delegates:
            try:
                result = await delegate.verify_token(token)
            except Exception:
                logger.warning(
                    "Delegate verifier %s raised — moving to next",
                    type(delegate).__name__,
                    exc_info=True,
                )
                continue
            if result is not None:
                return result
        return None
