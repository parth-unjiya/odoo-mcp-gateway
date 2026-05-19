"""Tests for the login tool's bearer-token issuance (Sprint 1, S1.4).

Verifies the integration point: when login succeeds, the response
includes ``bearer_token`` + ``token_type``, the token is registered
in ``gateway.token_index``, re-login rotates the token, and eviction
of a prior session revokes its tokens.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def _settings(**overrides: Any) -> Settings:
    defaults = {
        "odoo_url": "http://localhost:8069",
        "odoo_db": "testdb",
        "odoo_username": "",
        "odoo_api_key": SecretStr(""),
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_gateway() -> GatewayContext:
    cfg = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(_settings(), cfg)


def _auth_result(uid: int = 2, db: str = "testdb") -> AuthResult:
    return AuthResult(
        uid=uid,
        session_id="sess",
        user_context={},
        is_admin=False,
        groups=[],
        username="user",
        database=db,
    )


def _login_tool(gateway: GatewayContext) -> Any:
    """Build the login function via the real registration code."""
    server = MagicMock()
    captured: dict[str, Any] = {}

    def _tool() -> Any:
        def dec(fn: Any) -> Any:
            captured[fn.__name__] = fn
            return fn

        return dec

    server.tool = _tool
    from odoo_mcp_gateway.tools.auth import register_auth_tools

    register_auth_tools(server, gateway)
    return captured["login"]


class TestBearerTokenInResponse:
    @pytest.mark.asyncio
    async def test_login_returns_bearer_token(self) -> None:
        gw = _make_gateway()
        login = _login_tool(gw)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.login = AsyncMock(return_value=_auth_result(uid=2))
            mgr.close = AsyncMock()
            mgr.get_active_client = MagicMock(return_value=MagicMock())
            mgr.register_session = MagicMock()

            resp = await login(
                method="password",
                credential="pw",
                username="admin",
                database="testdb",
            )

        assert "bearer_token" in resp
        assert resp["token_type"] == "Bearer"
        assert isinstance(resp["bearer_token"], str)
        # 256-bit URL-safe → ~43 chars.
        assert 40 < len(resp["bearer_token"]) < 50
        # Token is in the gateway's index.
        assert gw.resolve_token(resp["bearer_token"]) == "2_testdb"

    @pytest.mark.asyncio
    async def test_relogin_rotates_token(self) -> None:
        gw = _make_gateway()
        login = _login_tool(gw)

        async def _do_login() -> dict[str, Any]:
            with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
                mgr = mock_cls.return_value
                mgr.login = AsyncMock(return_value=_auth_result(uid=2))
                mgr.close = AsyncMock()
                mgr.get_active_client = MagicMock(return_value=MagicMock())
                mgr.register_session = MagicMock()
                return await login(
                    method="password",
                    credential="pw",
                    username="admin",
                    database="testdb",
                )

        first = await _do_login()
        second = await _do_login()

        # Two different tokens issued.
        assert first["bearer_token"] != second["bearer_token"]
        # Old token revoked, new token valid.
        assert gw.resolve_token(first["bearer_token"]) is None
        assert gw.resolve_token(second["bearer_token"]) == "2_testdb"
        # token_index has exactly ONE entry for this session.
        assert sum(1 for sk in gw.token_index.values() if sk == "2_testdb") == 1

    @pytest.mark.asyncio
    async def test_different_uid_eviction_revokes_old_session_tokens(
        self,
    ) -> None:
        gw = _make_gateway()
        login = _login_tool(gw)

        # First login as uid=2.
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr1 = mock_cls.return_value
            mgr1.login = AsyncMock(return_value=_auth_result(uid=2))
            mgr1.close = AsyncMock()
            mgr1.get_active_client = MagicMock(return_value=MagicMock())
            mgr1.register_session = MagicMock()
            first = await login(
                method="password",
                credential="pw",
                username="admin",
                database="testdb",
            )

        first_token = first["bearer_token"]
        assert gw.resolve_token(first_token) == "2_testdb"

        # Second login as uid=5 (different user) — must evict uid=2 AND
        # revoke its tokens.
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr2 = mock_cls.return_value
            mgr2.login = AsyncMock(return_value=_auth_result(uid=5))
            mgr2.close = AsyncMock()
            mgr2.get_active_client = MagicMock(return_value=MagicMock())
            mgr2.register_session = MagicMock()
            second = await login(
                method="password",
                credential="pw",
                username="other",
                database="testdb",
            )

        # uid=2 session evicted, its token revoked.
        assert "2_testdb" not in gw.auth_managers
        assert gw.resolve_token(first_token) is None
        # uid=5 has a fresh token.
        assert gw.resolve_token(second["bearer_token"]) == "5_testdb"

    @pytest.mark.asyncio
    async def test_failed_login_issues_no_token(self) -> None:
        """A failed login must not leave a token in the index."""
        from odoo_mcp_gateway.client.exceptions import OdooAuthError

        gw = _make_gateway()
        login = _login_tool(gw)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr = mock_cls.return_value
            mgr.login = AsyncMock(side_effect=OdooAuthError("bad creds"))
            mgr.close = AsyncMock()

            resp = await login(
                method="password",
                credential="wrong",
                username="admin",
                database="testdb",
            )

        assert "error" in resp
        assert "bearer_token" not in resp
        assert gw.token_index == {}
