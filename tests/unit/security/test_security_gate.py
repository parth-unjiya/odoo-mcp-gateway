"""Tests for the security_gate() function in middleware.py."""

from __future__ import annotations

from unittest.mock import MagicMock

from odoo_mcp_gateway.core.security.middleware import security_gate


def _mock_gateway(
    *,
    groups: list[str] | None = None,
    is_admin: bool = False,
    uid: int = 0,
    login: str = "unknown",
) -> MagicMock:
    """Build a gateway mock with auth_managers that security_gate can read."""
    gateway = MagicMock()
    gateway.rate_limiter.check.return_value = (True, "")
    gateway.rbac.check_tool_access.return_value = None

    auth_result = MagicMock()
    auth_result.groups = groups or []
    auth_result.is_admin = is_admin
    auth_result.uid = uid
    auth_result.username = login

    auth_mgr = MagicMock()
    auth_mgr.auth_result = auth_result
    gateway.auth_managers = {"session": auth_mgr}
    return gateway


class TestSecurityGatePassesWhenAllChecksPass:
    async def test_returns_none_when_all_pass(self) -> None:
        gateway = _mock_gateway(groups=["base.group_user"], uid=1, login="testuser")
        result = await security_gate(gateway, "search_read", "session1")
        assert result is None

    async def test_read_tool_passes(self) -> None:
        gateway = _mock_gateway(uid=2, login="demo")
        result = await security_gate(gateway, "get_record", "session2")
        assert result is None

    async def test_write_tool_passes_when_within_limits(self) -> None:
        gateway = _mock_gateway(groups=["base.group_user"], uid=1, login="testuser")
        result = await security_gate(gateway, "create_record", "session1")
        assert result is None


class TestSecurityGateRateLimitBlocking:
    async def test_returns_error_on_rate_limit(self) -> None:
        gateway = _mock_gateway(uid=1)
        gateway.rate_limiter.check.return_value = (
            False,
            "Rate limit exceeded. Retry after 1.5 seconds.",
        )
        result = await security_gate(gateway, "search_read", "session1")
        assert result is not None
        assert "Rate limit exceeded" in result

    async def test_rate_limit_message_forwarded(self) -> None:
        gateway = _mock_gateway(uid=1)
        gateway.rate_limiter.check.return_value = (
            False,
            "Write rate limit exceeded. Retry after 2.0 seconds.",
        )
        result = await security_gate(gateway, "create_record", "session1")
        assert result is not None
        assert "Write rate limit" in result

    async def test_rate_limit_for_write_tool_sets_is_write(self) -> None:
        gateway = _mock_gateway(uid=1)
        await security_gate(gateway, "create_record", "session1")
        gateway.rate_limiter.check.assert_called_once_with("session1", is_write=True)

    async def test_rate_limit_for_read_tool_sets_is_write_false(self) -> None:
        gateway = _mock_gateway(uid=1)
        await security_gate(gateway, "search_read", "session1")
        gateway.rate_limiter.check.assert_called_once_with("session1", is_write=False)

    async def test_execute_method_is_write(self) -> None:
        gateway = _mock_gateway(uid=1)
        await security_gate(gateway, "execute_method", "session1")
        gateway.rate_limiter.check.assert_called_once_with("session1", is_write=True)


class TestSecurityGateRBACBlocking:
    async def test_returns_error_on_rbac_tool_denied(self) -> None:
        gateway = _mock_gateway(groups=["base.group_user"], uid=1)
        gateway.rate_limiter.check.return_value = (True, "")
        gateway.rbac.check_tool_access.return_value = (
            "Tool 'delete_record' requires one of: base.group_system"
        )
        result = await security_gate(gateway, "delete_record", "session1")
        assert result is not None
        assert "requires one of" in result

    async def test_rbac_passes_user_groups_and_admin_flag(self) -> None:
        gateway = _mock_gateway(
            groups=["base.group_user", "sales.group_sale_manager"],
            is_admin=True,
            uid=1,
            login="admin",
        )
        await security_gate(gateway, "search_read", "session1")
        gateway.rbac.check_tool_access.assert_called_once_with(
            "search_read",
            ["base.group_user", "sales.group_sale_manager"],
            True,
        )

    async def test_rbac_not_called_when_rate_limit_fails(self) -> None:
        gateway = _mock_gateway()
        gateway.rate_limiter.check.return_value = (False, "Rate limit exceeded")
        result = await security_gate(gateway, "search_read", "session1")
        assert result is not None
        gateway.rbac.check_tool_access.assert_not_called()


class TestSecurityGateGracefulDegradation:
    async def test_graceful_without_rate_limiter(self) -> None:
        """Login tool works without rate_limiter (auth not required)."""
        gateway = MagicMock(spec=[])
        result = await security_gate(gateway, "login_test")
        assert result is None

    async def test_graceful_without_rbac(self) -> None:
        """Authenticated user passes with rate_limiter but no rbac."""
        gateway = _mock_gateway(uid=1)
        gateway.rbac = None  # type: ignore[assignment]
        result = await security_gate(gateway, "search_read")
        assert result is None

    async def test_graceful_without_audit_logger(self) -> None:
        """Authenticated user passes with rate_limiter + rbac but no audit."""
        gateway = _mock_gateway(uid=1)
        gateway.audit_logger = None  # type: ignore[assignment]
        result = await security_gate(gateway, "search_read")
        assert result is None

    async def test_all_components_missing(self) -> None:
        """Login tool works with no security components at all."""
        gateway = MagicMock(spec=[])
        result = await security_gate(gateway, "login_anon", "session1")
        assert result is None

    async def test_unauthenticated_blocked_for_non_login(self) -> None:
        """Non-login tools require authentication (uid != 0)."""
        gateway = MagicMock(spec=[])
        result = await security_gate(gateway, "search_read")
        assert result is not None
        assert "Not authenticated" in result


class TestSecurityGateAuditLogging:
    async def test_audit_logger_called_on_success(self) -> None:
        gateway = _mock_gateway(uid=1, login="testuser")
        await security_gate(gateway, "create_record", "sess1")
        gateway.audit_logger.log.assert_called_once()

    async def test_audit_entry_contains_tool_name(self) -> None:
        gateway = _mock_gateway(uid=1, login="testuser")
        await security_gate(gateway, "search_read", "sess1")
        call_args = gateway.audit_logger.log.call_args[0][0]
        assert call_args.tool == "search_read"
        assert call_args.result == "allowed"

    async def test_audit_failure_does_not_block(self) -> None:
        gateway = _mock_gateway(uid=1)
        gateway.audit_logger.log.side_effect = RuntimeError("log fail")
        result = await security_gate(gateway, "search_read", "s1")
        assert result is None

    async def test_audit_logs_denied_on_rate_limit_failure(self) -> None:
        gateway = _mock_gateway(uid=1)
        gateway.rate_limiter.check.return_value = (False, "Rate limit exceeded")
        await security_gate(gateway, "search_read", "sess1")
        gateway.audit_logger.log.assert_called_once()
        entry = gateway.audit_logger.log.call_args[0][0]
        assert entry.result == "denied"

    async def test_audit_logs_denied_on_rbac_failure(self) -> None:
        gateway = _mock_gateway(uid=1)
        gateway.rbac.check_tool_access.return_value = "Access denied"
        result = await security_gate(gateway, "delete_record", "sess1")
        assert result is not None
        gateway.audit_logger.log.assert_called_once()
        entry = gateway.audit_logger.log.call_args[0][0]
        assert entry.result == "denied"


class TestSecurityGateDefaultSessionId:
    async def test_default_session_id(self) -> None:
        gateway = _mock_gateway(uid=1)
        await security_gate(gateway, "search_read")
        gateway.rate_limiter.check.assert_called_once_with("default", is_write=False)
