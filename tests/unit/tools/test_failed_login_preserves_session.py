"""Regression test for P2-8 / N5-5: failed login must NOT wipe session.

Before v0.2.2-final, the login tool evicted prior sessions BEFORE
attempting to authenticate. A typo'd password (or a malicious
wrong-credentials probe) consequently kicked out the legitimately
authenticated user — a self-DoS / hostile-DoS primitive.

The fix moves the eviction step AFTER ``AuthManager.login()`` succeeds
so a failed attempt is a no-op on existing sessions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.exceptions import OdooAuthError
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def _settings() -> Settings:
    from pydantic import SecretStr

    return Settings(
        odoo_url="http://localhost:8069",
        odoo_db="testdb",
        odoo_username="admin",
        odoo_api_key=SecretStr(""),
    )


def _gateway_config() -> GatewayConfig:
    return GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )


def _make_gateway() -> GatewayContext:
    return GatewayContext(_settings(), _gateway_config())


def _result(uid: int = 2) -> AuthResult:
    return AuthResult(
        uid=uid,
        session_id="sess",
        user_context={},
        is_admin=False,
        groups=[],
        username="user",
        database="testdb",
    )


def _login_tool(gateway: GatewayContext) -> Any:
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


class TestFailedLoginDoesNotWipeSession:
    async def test_wrong_password_keeps_existing_session(self) -> None:
        gateway = _make_gateway()
        login_fn = _login_tool(gateway)

        # First login: legitimate user (uid=2)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr1 = mock_cls.return_value
            mgr1.login = AsyncMock(return_value=_result(uid=2))
            mgr1.close = AsyncMock()
            mgr1.get_active_client = MagicMock(return_value=MagicMock())
            mgr1.register_session = MagicMock()
            await login_fn(
                method="password",
                credential="correct",
                username="admin",
                database="testdb",
            )

        assert "2_testdb" in gateway.auth_managers
        legit_mgr = gateway.auth_managers["2_testdb"]

        # Second login attempt: WRONG credentials → must NOT touch the legit session.
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            bad = mock_cls.return_value
            bad.login = AsyncMock(side_effect=OdooAuthError("bad credentials"))
            bad.close = AsyncMock()
            bad.register_session = MagicMock()
            resp = await login_fn(
                method="password",
                credential="wrong",
                username="attacker",
                database="testdb",
            )

        # Error reported but session intact
        assert "error" in resp
        assert gateway.auth_managers.get("2_testdb") is legit_mgr
        # Legit manager's close() MUST NOT have been called.
        legit_mgr.close.assert_not_called()  # type: ignore[attr-defined]

    async def test_successful_login_evicts_prior_session(self) -> None:
        """Sanity check that the eviction path still works on success."""
        gateway = _make_gateway()
        login_fn = _login_tool(gateway)

        # First login as uid=2
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr1 = mock_cls.return_value
            mgr1.login = AsyncMock(return_value=_result(uid=2))
            mgr1.close = AsyncMock()
            mgr1.get_active_client = MagicMock(return_value=MagicMock())
            mgr1.register_session = MagicMock()
            await login_fn(
                method="password",
                credential="pw",
                username="admin",
                database="testdb",
            )
        first = gateway.auth_managers["2_testdb"]

        # Now successfully log in as a DIFFERENT user.
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_cls:
            mgr2 = mock_cls.return_value
            mgr2.login = AsyncMock(return_value=_result(uid=5))
            mgr2.close = AsyncMock()
            mgr2.get_active_client = MagicMock(return_value=MagicMock())
            mgr2.register_session = MagicMock()
            await login_fn(
                method="password",
                credential="pw",
                username="other",
                database="testdb",
            )

        # Old session evicted, new session present.
        assert "2_testdb" not in gateway.auth_managers
        assert "5_testdb" in gateway.auth_managers
        first.close.assert_awaited()  # type: ignore[attr-defined]
