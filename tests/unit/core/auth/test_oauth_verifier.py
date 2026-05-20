"""Tests for OAuth 2.1 JWT verifier (ADR-005 Sprint 4)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.auth.oauth_verifier import (
    OAUTH_AVAILABLE,
    CompositeTokenVerifier,
    OAuthJwtVerifier,
)
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext

pytestmark = pytest.mark.skipif(
    not OAUTH_AVAILABLE,
    reason="authlib not installed; install [oauth] extras",
)


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


def _stub_session(gw: GatewayContext, session_key: str, username: str) -> None:
    """Attach a fake auth_manager with auth_result.username = email."""
    mgr = MagicMock()
    mgr.auth_result = MagicMock(username=username, uid=int(session_key.split("_")[0]))
    gw.auth_managers[session_key] = mgr


def _verifier(gw: GatewayContext) -> OAuthJwtVerifier:
    return OAuthJwtVerifier(
        gateway=gw,
        issuer="https://idp.example.com/realm/master",
        audience="https://mcp.example.com/",
        jwks_uri="https://idp.example.com/.well-known/jwks.json",
    )


class TestEmailClaimMapping:
    @pytest.mark.asyncio
    async def test_known_email_resolves_to_session_key(self) -> None:
        gw = _make_gateway()
        _stub_session(gw, "5_db", "alice@example.com")
        verifier = _verifier(gw)
        # Patch the decode step to return claims directly.
        claims = {
            "iss": "https://idp.example.com/realm/master",
            "aud": "https://mcp.example.com/",
            "exp": int(time.time()) + 3600,
            "email": "alice@example.com",
            "scope": "odoo.read odoo.write",
        }
        with patch.object(
            verifier, "_decode_and_validate", AsyncMock(return_value=claims)
        ):
            result = await verifier.verify_token("dummy-jwt")
        assert result is not None
        assert result.client_id == "5_db"
        assert "odoo.read" in result.scopes
        assert "odoo.write" in result.scopes
        # Scopes not in DEFAULT_OAUTH_SCOPES are filtered out.

    @pytest.mark.asyncio
    async def test_unknown_email_rejected(self) -> None:
        gw = _make_gateway()
        _stub_session(gw, "5_db", "alice@example.com")
        verifier = _verifier(gw)
        claims = {
            "iss": "https://idp.example.com/realm/master",
            "aud": "https://mcp.example.com/",
            "exp": int(time.time()) + 3600,
            "email": "stranger@example.com",  # not in any session
        }
        with patch.object(
            verifier, "_decode_and_validate", AsyncMock(return_value=claims)
        ):
            result = await verifier.verify_token("dummy-jwt")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_email_falls_back_to_preferred_username(self) -> None:
        gw = _make_gateway()
        _stub_session(gw, "5_db", "alice@example.com")
        verifier = _verifier(gw)
        claims = {
            "iss": "https://idp.example.com/realm/master",
            "aud": "https://mcp.example.com/",
            "exp": int(time.time()) + 3600,
            "preferred_username": "alice@example.com",
        }
        with patch.object(
            verifier, "_decode_and_validate", AsyncMock(return_value=claims)
        ):
            result = await verifier.verify_token("dummy-jwt")
        assert result is not None
        assert result.client_id == "5_db"

    @pytest.mark.asyncio
    async def test_no_email_claim_rejected(self) -> None:
        gw = _make_gateway()
        verifier = _verifier(gw)
        claims = {
            "iss": "https://idp.example.com/realm/master",
            "aud": "https://mcp.example.com/",
            "exp": int(time.time()) + 3600,
        }
        with patch.object(
            verifier, "_decode_and_validate", AsyncMock(return_value=claims)
        ):
            result = await verifier.verify_token("dummy-jwt")
        assert result is None


class TestEmptyToken:
    @pytest.mark.asyncio
    async def test_empty_token_returns_none(self) -> None:
        gw = _make_gateway()
        verifier = _verifier(gw)
        assert await verifier.verify_token("") is None


class TestInvalidJwt:
    @pytest.mark.asyncio
    async def test_decode_failure_returns_none(self) -> None:
        gw = _make_gateway()
        verifier = _verifier(gw)
        with patch.object(
            verifier,
            "_decode_and_validate",
            AsyncMock(side_effect=RuntimeError("bad sig")),
        ):
            result = await verifier.verify_token("malformed.jwt.token")
        assert result is None


class TestScopeIntersection:
    @pytest.mark.asyncio
    async def test_scopes_filtered_to_known_set(self) -> None:
        gw = _make_gateway()
        _stub_session(gw, "5_db", "alice@example.com")
        verifier = _verifier(gw)
        claims = {
            "iss": "https://idp.example.com/realm/master",
            "aud": "https://mcp.example.com/",
            "exp": int(time.time()) + 3600,
            "email": "alice@example.com",
            "scope": "odoo.read random.thing odoo.admin",
        }
        with patch.object(
            verifier, "_decode_and_validate", AsyncMock(return_value=claims)
        ):
            result = await verifier.verify_token("dummy-jwt")
        assert result is not None
        # Unknown 'random.thing' is dropped; known scopes pass through.
        assert "odoo.read" in result.scopes
        assert "odoo.admin" in result.scopes
        assert "random.thing" not in result.scopes

    @pytest.mark.asyncio
    async def test_no_scope_claim_grants_default(self) -> None:
        gw = _make_gateway()
        _stub_session(gw, "5_db", "alice@example.com")
        verifier = _verifier(gw)
        claims = {
            "iss": "https://idp.example.com/realm/master",
            "aud": "https://mcp.example.com/",
            "exp": int(time.time()) + 3600,
            "email": "alice@example.com",
        }
        with patch.object(
            verifier, "_decode_and_validate", AsyncMock(return_value=claims)
        ):
            result = await verifier.verify_token("dummy-jwt")
        assert result is not None
        # No scope claim → DEFAULT_OAUTH_SCOPES granted.
        assert "odoo.read" in result.scopes
        assert "odoo.write" in result.scopes
        assert "odoo.admin" in result.scopes


class TestCompositeVerifier:
    @pytest.mark.asyncio
    async def test_first_delegate_wins(self) -> None:
        opaque = MagicMock()
        opaque.verify_token = AsyncMock(return_value=MagicMock(client_id="opaque-1"))
        jwt = MagicMock()
        jwt.verify_token = AsyncMock(return_value=MagicMock(client_id="jwt-1"))

        composite = CompositeTokenVerifier([opaque, jwt])
        result = await composite.verify_token("any-token")
        assert result is not None
        assert result.client_id == "opaque-1"
        # JWT delegate must NOT have been called.
        jwt.verify_token.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_through_to_next_delegate(self) -> None:
        opaque = MagicMock()
        opaque.verify_token = AsyncMock(return_value=None)
        jwt = MagicMock()
        jwt.verify_token = AsyncMock(return_value=MagicMock(client_id="jwt-1"))

        composite = CompositeTokenVerifier([opaque, jwt])
        result = await composite.verify_token("jwt-shaped-token")
        assert result is not None
        assert result.client_id == "jwt-1"

    @pytest.mark.asyncio
    async def test_all_none_returns_none(self) -> None:
        a = MagicMock()
        a.verify_token = AsyncMock(return_value=None)
        b = MagicMock()
        b.verify_token = AsyncMock(return_value=None)
        composite = CompositeTokenVerifier([a, b])
        assert await composite.verify_token("unknown") is None

    @pytest.mark.asyncio
    async def test_delegate_exception_swallowed(self) -> None:
        a = MagicMock()
        a.verify_token = AsyncMock(side_effect=RuntimeError("boom"))
        b = MagicMock()
        b.verify_token = AsyncMock(return_value=MagicMock(client_id="b-1"))
        composite = CompositeTokenVerifier([a, b])
        result = await composite.verify_token("any")
        assert result is not None
        assert result.client_id == "b-1"

    def test_empty_delegates_rejected(self) -> None:
        from odoo_mcp_gateway.core.auth.oauth_verifier import OAuthVerifierError

        with pytest.raises(OAuthVerifierError):
            CompositeTokenVerifier([])
