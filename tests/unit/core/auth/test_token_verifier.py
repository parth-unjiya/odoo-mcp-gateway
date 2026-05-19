"""Tests for OdooTokenVerifier (Sprint 1, S1.2).

The verifier is the bridge between the MCP SDK's BearerAuthBackend and
the gateway's session model. These tests pin its contract:

* Valid token + live session → returns AccessToken with client_id == session_key.
* Unknown token → returns None (SDK responds with 401).
* Empty / falsy token → returns None.
* Token bound to an evicted session → returns None AND revokes the
  dangling token defensively.
* AccessToken.scopes uses the constructor-supplied list (default
  ``["odoo.session"]``), passed by reference-safe copy.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.auth.token_verifier import OdooTokenVerifier
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def _make_gateway() -> GatewayContext:
    settings = Settings(
        odoo_url="http://localhost:8069",
        odoo_db="test",
        odoo_username="admin",
        odoo_api_key=SecretStr(""),
    )
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, cfg)


def _register_live_session(gw: GatewayContext, session_key: str) -> str:
    """Wire a token to a live (mock) AuthManager so the verifier accepts it."""
    gw.auth_managers[session_key] = MagicMock(name=f"mgr_{session_key}")
    return gw.issue_bearer_token(session_key)


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_valid_token_returns_access_token(self) -> None:
        gw = _make_gateway()
        token = _register_live_session(gw, "2_db")
        verifier = OdooTokenVerifier(gw)

        access = await verifier.verify_token(token)
        assert access is not None
        assert access.token == token
        assert access.client_id == "2_db"
        assert "odoo.session" in access.scopes
        # No expires_at — session lifetime enforced upstream.
        assert access.expires_at is None

    @pytest.mark.asyncio
    async def test_unknown_token_returns_none(self) -> None:
        gw = _make_gateway()
        verifier = OdooTokenVerifier(gw)
        assert await verifier.verify_token("bogus-token-xyz") is None

    @pytest.mark.asyncio
    async def test_empty_token_returns_none(self) -> None:
        gw = _make_gateway()
        verifier = OdooTokenVerifier(gw)
        assert await verifier.verify_token("") is None

    @pytest.mark.asyncio
    async def test_orphaned_token_revoked_and_rejected(self) -> None:
        """Token bound to a session that's no longer in auth_managers
        must be refused AND removed from the index."""
        gw = _make_gateway()
        token = _register_live_session(gw, "2_db")
        # Evict the AuthManager without revoking tokens (simulates a bug).
        del gw.auth_managers["2_db"]
        # Token still in index pre-call.
        assert token in gw.token_index

        verifier = OdooTokenVerifier(gw)
        assert await verifier.verify_token(token) is None
        # Verifier defensively cleaned up the dangling token.
        assert token not in gw.token_index

    @pytest.mark.asyncio
    async def test_custom_scopes_returned(self) -> None:
        gw = _make_gateway()
        token = _register_live_session(gw, "2_db")
        verifier = OdooTokenVerifier(gw, scopes=["odoo.read", "odoo.write"])

        access = await verifier.verify_token(token)
        assert access is not None
        assert access.scopes == ["odoo.read", "odoo.write"]

    @pytest.mark.asyncio
    async def test_scopes_copied_not_shared(self) -> None:
        """Mutating the returned AccessToken's scopes list must not
        affect future verifications."""
        gw = _make_gateway()
        token = _register_live_session(gw, "2_db")
        verifier = OdooTokenVerifier(gw, scopes=["odoo.session"])

        access1 = await verifier.verify_token(token)
        assert access1 is not None
        access1.scopes.append("malicious.scope")  # type: ignore[union-attr]

        # New verification must NOT include the mutation.
        access2 = await verifier.verify_token(token)
        assert access2 is not None
        assert "malicious.scope" not in access2.scopes
