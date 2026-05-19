"""Tests for the login tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
        # The login response must always expose ``is_admin`` so the client
        # can render admin-only UI affordances correctly.
        assert "is_admin" in resp
        assert resp["is_admin"] is False

    async def test_response_includes_is_admin_true_for_admin(self) -> None:
        """Admin login responses must surface ``is_admin=True`` to the client."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=1, username="admin", is_admin=True)
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

        assert "is_admin" in resp
        assert resp["is_admin"] is True

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


# ------------------------------------------------------------------
# Auth input validation tests
# ------------------------------------------------------------------


class TestAuthInputValidation:
    """Verify credential length validation in the login tool."""

    async def test_username_too_long_returns_error(self) -> None:
        """Username longer than 256 chars should be rejected."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        long_username = "a" * 257
        resp = await login_fn(
            method="password",
            credential="password123",
            username=long_username,
            database="testdb",
        )

        assert "error" in resp
        assert "Username too long" in resp["error"]

    async def test_credential_too_long_returns_error(self) -> None:
        """Credential longer than 4096 chars should be rejected."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        long_credential = "x" * 4097
        resp = await login_fn(
            method="api_key",
            credential=long_credential,
            username="admin",
            database="testdb",
        )

        assert "error" in resp
        assert "Credential too long" in resp["error"]

    async def test_username_at_max_length_is_accepted(self) -> None:
        """Username exactly at 256 chars should be accepted (not rejected)."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        max_username = "a" * 256
        result = _auth_result(uid=5, username=max_username)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="password",
                credential="pass",
                username=max_username,
                database="testdb",
            )

        # Should NOT be an error -- length is exactly at the limit
        assert "error" not in resp or "too long" not in resp.get("error", "").lower()

    async def test_credential_at_max_length_is_accepted(self) -> None:
        """Credential exactly at 4096 chars should be accepted (not rejected)."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        max_credential = "x" * 4096
        result = _auth_result(uid=5)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="api_key",
                credential=max_credential,
                username="admin",
                database="testdb",
            )

        # Should NOT be an error -- length is exactly at the limit
        assert "error" not in resp or "too long" not in resp.get("error", "").lower()


# ------------------------------------------------------------------
# Settings → AuthManager wiring
# ------------------------------------------------------------------


class TestSettingsWiring:
    """Verify AuthManager receives Settings values rather than defaults."""

    async def test_session_timeout_passed_to_auth_manager(self) -> None:
        """``session_timeout_seconds`` from Settings must flow into AuthManager."""
        gateway = _make_gateway(session_timeout_seconds=42)
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=1)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="testdb",
            )

        # Verify the constructor received the configured timeout.
        kwargs = mock_auth_cls.call_args.kwargs
        assert kwargs["session_timeout_seconds"] == 42

    async def test_max_concurrent_sessions_passed_to_auth_manager(self) -> None:
        """``max_concurrent_sessions`` from Settings must flow into AuthManager."""
        gateway = _make_gateway(max_concurrent_sessions=7)
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=1)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="testdb",
            )

        kwargs = mock_auth_cls.call_args.kwargs
        assert kwargs["max_concurrent_sessions"] == 7


# ------------------------------------------------------------------
# IP / source-level rate limiting
# ------------------------------------------------------------------


class TestSourceRateLimit:
    """Verify IP/source-level rate limiting wires into login.

    The source bucket key on stdio is now ``stdio:<pid>`` (per-process)
    so that a self-DoS on one process doesn't cascade across every
    process sharing a host. Tests resolve the expected key dynamically.
    """

    @staticmethod
    def _stdio_key() -> str:
        import os

        return f"stdio:{os.getpid()}"

    async def test_source_lockout_blocks_login(self) -> None:
        """Pre-populating the IP rate limiter with failures should block login."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        # Saturate the source bucket. Default threshold is 30 failures.
        key = self._stdio_key()
        for _ in range(30):
            gateway.login_ip_rate_limiter.record_failure(key)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=_auth_result())

            resp = await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="testdb",
            )

        # The login attempt should be blocked before AuthManager.login runs.
        assert "error" in resp
        assert "Too many failed login attempts from this source" in resp["error"]
        instance.login.assert_not_called()

    async def test_source_failure_recorded_on_auth_error(self) -> None:
        """An OdooAuthError must record failures on BOTH limiters."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(side_effect=OdooAuthError("bad creds"))

            await login_fn(
                method="password",
                credential="wrong",
                username="alice",
                database="testdb",
            )

        # Username failure recorded.
        assert gateway.login_rate_limiter._failures.get("alice") is not None
        # Source (per-PID) failure recorded.
        assert (
            gateway.login_ip_rate_limiter._failures.get(self._stdio_key()) is not None
        )

    async def test_source_success_resets_counter(self) -> None:
        """A successful login must clear the source failure counter."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        key = self._stdio_key()
        # Pre-load 5 failures (well under the threshold of 30).
        for _ in range(5):
            gateway.login_ip_rate_limiter.record_failure(key)
        assert key in gateway.login_ip_rate_limiter._failures

        result = _auth_result(uid=1)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            instance = mock_auth_cls.return_value
            instance.login = AsyncMock(return_value=result)

            resp = await login_fn(
                method="password",
                credential="pass",
                username="admin",
                database="testdb",
            )

        assert "error" not in resp
        assert key not in gateway.login_ip_rate_limiter._failures


# ------------------------------------------------------------------
# Session isolation (single-user-per-process enforcement)
# ------------------------------------------------------------------


class TestSessionIsolation:
    """When a different user logs in, the previous user's session is closed.

    This is the single-user-per-process enforcement that prevents the
    cross-user data contamination revealed by the Odoo 19 multi-role
    swarm testing. See helpers.py changes for the corresponding refusal
    to fall through to "first session" in tools.
    """

    async def test_different_uid_closes_prior_session(self) -> None:
        """Logging in as uid=5 after uid=2 must close uid=2's session."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        # First login: uid=2
        result1 = _auth_result(uid=2)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            mgr1 = mock_auth_cls.return_value
            mgr1.login = AsyncMock(return_value=result1)
            mgr1.close = AsyncMock()
            mgr1.get_active_client = MagicMock(return_value=MagicMock())

            await login_fn(
                method="password",
                credential="pwd",
                username="admin",
                database="testdb",
            )

        # auth_managers should have exactly one entry now
        assert len(gateway.auth_managers) == 1
        assert "2_testdb" in gateway.auth_managers
        first_mgr = gateway.auth_managers["2_testdb"]

        # Second login: uid=5 (DIFFERENT user)
        result2 = _auth_result(uid=5)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            mgr2 = mock_auth_cls.return_value
            mgr2.login = AsyncMock(return_value=result2)
            mgr2.close = AsyncMock()
            mgr2.get_active_client = MagicMock(return_value=MagicMock())

            await login_fn(
                method="password",
                credential="pwd",
                username="user5",
                database="testdb",
            )

        # The old session (uid=2) must have been closed and removed
        assert "2_testdb" not in gateway.auth_managers
        # The new session (uid=5) is now the only entry
        assert len(gateway.auth_managers) == 1
        assert "5_testdb" in gateway.auth_managers
        # The OLD manager's close() was awaited
        first_mgr.close.assert_awaited()

    async def test_same_uid_relogin_keeps_single_entry(self) -> None:
        """Re-login as the same uid replaces the entry but doesn't accumulate."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=2)
        # First login
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            mgr = mock_auth_cls.return_value
            mgr.login = AsyncMock(return_value=result)
            mgr.close = AsyncMock()
            # AuthManager.get_active_client is synchronous — using MagicMock
            # (not AsyncMock) avoids leaving an unawaited coroutine when the
            # post-login version probe calls it.
            mgr.get_active_client = MagicMock(return_value=MagicMock())

            await login_fn(
                method="password",
                credential="pwd",
                username="admin",
                database="testdb",
            )

        # Re-login (same uid)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            mgr2 = mock_auth_cls.return_value
            mgr2.login = AsyncMock(return_value=result)
            mgr2.close = AsyncMock()
            mgr2.get_active_client = MagicMock(return_value=MagicMock())

            await login_fn(
                method="password",
                credential="pwd",
                username="admin",
                database="testdb",
            )

        # Still exactly one entry, replaced
        assert len(gateway.auth_managers) == 1
        assert "2_testdb" in gateway.auth_managers

    async def test_is_admin_in_login_response(self) -> None:
        """Login response must include is_admin field (was missing pre-fix)."""
        gateway = _make_gateway()
        login_fn = _get_login_tool(gateway)

        result = _auth_result(uid=2, is_admin=True)
        with patch("odoo_mcp_gateway.tools.auth.AuthManager") as mock_auth_cls:
            mgr = mock_auth_cls.return_value
            mgr.login = AsyncMock(return_value=result)
            mgr.close = AsyncMock()
            # AuthManager.get_active_client is synchronous — using MagicMock
            # (not AsyncMock) avoids leaving an unawaited coroutine when the
            # post-login version probe calls it.
            mgr.get_active_client = MagicMock(return_value=MagicMock())

            resp = await login_fn(
                method="password",
                credential="pwd",
                username="admin",
                database="testdb",
            )

        assert "is_admin" in resp
        assert resp["is_admin"] is True
