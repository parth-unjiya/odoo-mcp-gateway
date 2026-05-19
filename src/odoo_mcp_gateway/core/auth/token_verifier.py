"""Bearer-token verifier that bridges MCP SDK auth to our session model.

The MCP SDK ships ``mcp.server.auth.provider.TokenVerifier`` — a protocol
the SDK's ``BearerAuthBackend`` calls on every HTTP request to validate
an ``Authorization: Bearer <token>`` header. We implement it against
``GatewayContext.token_index`` so the existing single-user-per-process
session model carries forward to multi-user HTTP transport unchanged.

The verifier is intentionally minimal:

* No JWT signature math here — tokens are opaque URL-safe strings that
  only have meaning inside this process. (OAuth 2.1 JWT support lands
  in a sibling verifier in ADR-005.)
* No expiry tracking inside the verifier — the gateway's existing
  session-timeout machinery (see ``AuthManager._is_session_expired``)
  invalidates the underlying ``AuthManager`` lazily; when that happens,
  ``token_index`` should be cleared via ``revoke_session_tokens``. The
  verifier merely reports "no, this token is unknown" after revocation.
* Returns the SDK's ``AccessToken`` with ``client_id`` set to the
  internal ``session_key`` (``"<uid>_<db>"``). The downstream
  ``SessionResolverMiddleware`` projects that into our private
  ``_current_session_key`` ContextVar so every existing tool resolves
  the right user without any signature change.

This keeps stdio mode untouched (no token issuance, no verifier
involvement) and adds zero per-request latency beyond a dict lookup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken, TokenVerifier

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

# Default scope granted to every successfully-authenticated session in
# v0.3.0. Fine-grained scope mapping (per-tool, per-model) lands in the
# OAuth ADR; until then we use a single coarse scope so the SDK's
# scope-enforcement layer doesn't reject the request.
_DEFAULT_SCOPES: list[str] = ["odoo.session"]


class OdooTokenVerifier(TokenVerifier):
    """Validates opaque bearer tokens against ``GatewayContext.token_index``.

    Lifecycle alignment with the gateway:
    * Tokens are minted in the ``login`` tool after a successful
      ``AuthManager.login()``. They are bound to the resulting
      ``session_key`` and rotated on re-login (see
      ``GatewayContext.issue_bearer_token``).
    * Tokens are revoked when the session is evicted by single-user-
      per-process enforcement or when the gateway shuts down (see
      ``GatewayContext.cleanup``).
    * The verifier does NOT mutate gateway state. It only reads.
    """

    def __init__(
        self, gateway: GatewayContext, scopes: list[str] | None = None
    ) -> None:
        self._gateway = gateway
        # Copy to avoid surprising mutation if the caller passes a
        # list literal and later edits it.
        self._scopes: list[str] = (
            list(scopes) if scopes is not None else list(_DEFAULT_SCOPES)
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an ``AccessToken`` for *token*, or ``None`` if invalid.

        The SDK's ``BearerAuthBackend`` calls this on every HTTP request.
        Returning ``None`` causes the SDK to respond with HTTP 401 +
        ``WWW-Authenticate`` — exactly the right behaviour.
        """
        if not token:
            return None

        session_key = self._gateway.resolve_token(token)
        if session_key is None:
            # Don't log the actual token (sensitive); a hash prefix is
            # plenty for correlation during debugging.
            logger.debug(
                "Unknown bearer token presented (prefix=%s...)",
                token[:8],
            )
            return None

        # Sanity-check the session still exists. If the auth_manager was
        # popped without revoking its tokens (e.g. a bug), surface that
        # by refusing the token rather than silently authenticating an
        # orphaned session.
        if session_key not in self._gateway.auth_managers:
            logger.warning(
                "Token bound to evicted session %s — refusing and revoking",
                session_key,
            )
            self._gateway.revoke_token(token)
            return None

        return AccessToken(
            token=token,
            client_id=session_key,
            scopes=list(self._scopes),
            expires_at=None,  # Expiry is enforced by AuthManager session timeout.
            resource=None,
        )
