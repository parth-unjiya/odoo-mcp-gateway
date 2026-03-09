"""Tests for the login tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from odoo_mcp_gateway.client.base import AuthResult
from odoo_mcp_gateway.client.exceptions import OdooAuthError, OdooConnectionError
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _auth_result(**overrides: Any) -> AuthResult:
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": "s1",
        "user_context": {"lang": "en_US"},
        "is_admin": False,
        "groups": ["base.group_user"],
        "username": "admin",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def _make_gateway(**settings_overrides: Any) -> GatewayContext:
    settings_defaults = {
        "odoo_url": "http://localhost:8069",
        "odoo_db": "testdb",
        "odoo_username": "",
        "odoo_api_key": SecretStr(""),
    }
    settings_defaults.update(settings_overrides)
    settings = Settings(**settings_defaults)
    config = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return GatewayContext(settings, config)


def _get_login_tool(gateway: GatewayContext) -> Any:
    """Build the login tool function by registering on a mock server."""
    from mcp.server.fastmcp import FastMCP

    from odoo_mcp_gateway.tools.auth import register_auth_tools

    server = FastMCP(name="test")
    register_auth_tools(server, gateway)
    # Extract the registered tool function from the closure
    # The tool is stored in the server's tool manager
    tool_fn = None
    for name, tool in server._tool_manager._tools.items():
        if name == "login":
            tool_fn = tool.fn
            break
    assert tool_fn is not None, "login tool not registered"
    return tool_fn


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestLoginApiKey:
    async def test_success(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=10, username="admin")
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)
            instance.auth_result = result

            resp = await login_fn(
                method="api_key",
                credential="my-api-key",
                username="admin",
                database="testdb",
            )

        assert resp["uid"] == 10
        assert resp["user"] == "admin"
        assert resp["method"] == "api_key"

    async def test_stores_auth_manager(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=10)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)
            instance.auth_result = result

            await login_fn(
                method="api_key",
                credential="key",
                username="admin",
                database="testdb",
            )

        assert len(gateway.auth_managers) == 1

    async def test_invalid_key_returns_error(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(
                side_effect=OdooAuthError("bad key"),
            )

            resp = await login_fn(
                method="api_key",
                credential="wrong",
                username="admin",
                database="testdb",
            )

        assert "error" in resp
        assert "Authentication failed" in resp["error"]


class TestLoginPassword:
    async def test_success(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=2, session_id="sess-abc")
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="password",
                credential="secret",
                username="admin",
                database="testdb",
            )

        assert resp["uid"] == 2
        assert resp["method"] == "password"

    async def test_invalid_password_returns_error(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(
                side_effect=OdooAuthError("invalid credentials"),
            )

            resp = await login_fn(
                method="password",
                credential="wrong",
                username="admin",
                database="testdb",
            )

        assert "error" in resp
        assert "Authentication failed" in resp["error"]


class TestLoginSession:
    async def test_success(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=42, is_admin=True, username="admin")
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="session",
                credential="session-token",
                database="testdb",
            )

        assert resp["uid"] == 42
        assert resp["method"] == "session"


class TestLoginValidation:
    async def test_unknown_method_returns_error(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        resp = await login_fn(
            method="magic",
            credential="x",
            username="u",
            database="db",
        )

        assert "error" in resp
        assert "Unknown auth method" in resp["error"]

    async def test_no_database_returns_error(self) -> None:
        gateway = _make_gateway(odoo_db="")
        login_fn = _get_login_tool(gateway)

        resp = await login_fn(
            method="password",
            credential="x",
            username="u",
            database="",
        )

        assert "error" in resp
        assert "database" in resp["error"].lower()

    async def test_uses_default_database(self) -> None:
        gateway = _make_gateway(odoo_db="default_db")
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=5, database="default_db")
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="",
            )

        assert resp["database"] == "default_db"

    async def test_connection_error_returns_error(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(
                side_effect=OdooConnectionError("cannot connect"),
            )

            resp = await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="testdb",
            )

        assert "error" in resp

    async def test_returns_groups(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(
            uid=5,
            groups=["base.group_user", "sales.group_sale_manager"],
        )
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="api_key",
                credential="key",
                username="admin",
                database="testdb",
            )

        assert "base.group_user" in resp["groups"]
        assert "sales.group_sale_manager" in resp["groups"]

    async def test_unexpected_error_returns_error(self) -> None:
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(
                side_effect=RuntimeError("unexpected"),
            )

            resp = await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="testdb",
            )

        assert "error" in resp
        assert resp["error"]  # sanitized error message returned
