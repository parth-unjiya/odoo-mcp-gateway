"""Tests for the HR domain plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.plugins.core.helpers import next_month
from odoo_mcp_gateway.plugins.core.hr import HRPlugin

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_context():
    """Create a mock gateway context with auth manager and client."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(uid=42)
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    # Disable security_gate sub-checks so tests focus on plugin logic
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    return ctx, client


@pytest.fixture
def unauth_context():
    """Create a mock context with no auth managers (unauthenticated)."""
    ctx = MagicMock()
    ctx.auth_managers = {}
    ctx.rate_limiter = None
    ctx.audit_logger = None
    return ctx


@pytest.fixture
def tools(mock_context):
    """Register HR plugin and capture all tool functions."""
    ctx, _ = mock_context
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = HRPlugin()
    plugin.register(server, ctx)
    return captured


@pytest.fixture
def unauth_tools(unauth_context):
    """Register HR plugin with unauthenticated context."""
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = HRPlugin()
    plugin.register(server, unauth_context)
    return captured


# ── Plugin metadata ──────────────────────────────────────────────


class TestHRPluginMetadata:
    def test_name(self):
        plugin = HRPlugin()
        assert plugin.name == "hr"

    def test_description(self):
        plugin = HRPlugin()
        assert "attendance" in plugin.description.lower()

    def test_required_odoo_modules(self):
        plugin = HRPlugin()
        assert "hr" in plugin.required_odoo_modules
        assert "hr_attendance" in plugin.required_odoo_modules
        assert "hr_holidays" in plugin.required_odoo_modules

    def test_required_models(self):
        plugin = HRPlugin()
        assert "hr.employee" in plugin.required_models
        assert "hr.attendance" in plugin.required_models
        assert "hr.leave" in plugin.required_models


# ── check_in tests ───────────────────────────────────────────────


class TestCheckIn:
    async def test_check_in_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],  # employee search
            [],  # no open attendance
            42,  # create returns id
        ]
        result = await tools["check_in"]()
        assert result["status"] == "checked_in"
        assert result["attendance_id"] == 42
        assert result["employee"] == "John"
        assert "check_in" in result

    async def test_check_in_already_checked_in(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [{"id": 10, "check_in": "2025-01-01 08:00:00"}],
        ]
        result = await tools["check_in"]()
        assert result["error"] == "Already checked in"
        assert result["attendance_id"] == 10
        assert result["check_in_time"] == "2025-01-01 08:00:00"

    async def test_check_in_no_employee(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],  # no employee found
        ]
        result = await tools["check_in"]()
        assert "No employee record found" in result["error"]

    async def test_check_in_not_authenticated(self, unauth_tools):
        result = await unauth_tools["check_in"]()
        assert result["error"] == "Not authenticated"

    async def test_check_in_handles_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Connection lost")
        result = await tools["check_in"]()
        assert "Connection lost" in result["error"]


# ── check_out tests ──────────────────────────────────────────────


class TestCheckOut:
    async def test_check_out_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [{"id": 10, "check_in": "2025-01-01 08:00:00"}],
            True,  # write returns True
        ]
        result = await tools["check_out"]()
        assert result["status"] == "checked_out"
        assert result["attendance_id"] == 10
        assert result["employee"] == "John"
        assert result["check_in"] == "2025-01-01 08:00:00"
        assert "check_out" in result

    async def test_check_out_not_checked_in(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [],  # no open attendance
        ]
        result = await tools["check_out"]()
        assert "Not checked in" in result["error"]

    async def test_check_out_no_employee(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],  # no employee found
        ]
        result = await tools["check_out"]()
        assert "No employee record found" in result["error"]

    async def test_check_out_not_authenticated(self, unauth_tools):
        result = await unauth_tools["check_out"]()
        assert result["error"] == "Not authenticated"


# ── get_my_attendance tests ──────────────────────────────────────


class TestGetMyAttendance:
    async def test_returns_records(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1}],  # employee search
            [
                {
                    "check_in": "2025-01-01 08:00:00",
                    "check_out": "2025-01-01 17:00:00",
                    "worked_hours": 9.0,
                },
            ],
        ]
        result = await tools["get_my_attendance"]()
        assert result["count"] == 1
        assert len(result["records"]) == 1

    async def test_with_month_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1}],
            [],
        ]
        result = await tools["get_my_attendance"](month="2025-03")
        assert result["count"] == 0
        # Verify domain included month filter
        call_args = client.execute_kw.call_args_list[1]
        domain = call_args[0][2][0]
        assert ["check_in", ">=", "2025-03-01 00:00:00"] in domain
        assert ["check_in", "<", "2025-04-01 00:00:00"] in domain

    async def test_limit_capped_at_100(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1}],
            [],
        ]
        await tools["get_my_attendance"](limit=500)
        call_args = client.execute_kw.call_args_list[1]
        kwargs = call_args[0][3]
        assert kwargs["limit"] == 100

    async def test_no_employee(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],  # no employee
        ]
        result = await tools["get_my_attendance"]()
        assert "No employee record found" in result["error"]


# ── get_my_leaves tests ──────────────────────────────────────────


class TestGetMyLeaves:
    async def test_returns_records(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1}],
            [
                {
                    "name": "Vacation",
                    "holiday_status_id": [1, "Annual Leave"],
                    "date_from": "2025-07-01",
                    "date_to": "2025-07-05",
                    "number_of_days": 5,
                    "state": "validate",
                },
            ],
        ]
        result = await tools["get_my_leaves"]()
        assert result["count"] == 1
        assert result["records"][0]["name"] == "Vacation"

    async def test_with_state_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1}],
            [],
        ]
        result = await tools["get_my_leaves"](state="draft")
        assert result["count"] == 0
        # Verify state domain was added
        call_args = client.execute_kw.call_args_list[1]
        domain = call_args[0][2][0]
        assert ["state", "=", "draft"] in domain

    async def test_no_employee(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],
        ]
        result = await tools["get_my_leaves"]()
        assert "No employee record found" in result["error"]


# ── request_leave tests ──────────────────────────────────────────


class TestRequestLeave:
    async def test_request_leave_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            99,  # leave ID
        ]
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="2025-07-05",
            reason="Summer holiday",
        )
        assert result["status"] == "created"
        assert result["leave_id"] == 99
        assert result["employee"] == "John"
        assert result["date_from"] == "2025-07-01"
        assert result["date_to"] == "2025-07-05"

        # Verify create was called with reason
        create_call = client.execute_kw.call_args_list[1]
        values = create_call[0][2][0]
        assert values["name"] == "Summer holiday"

    async def test_request_leave_no_reason(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            100,
        ]
        result = await tools["request_leave"](
            leave_type_id=2,
            date_from="2025-08-01",
            date_to="2025-08-02",
        )
        assert result["status"] == "created"
        # Verify no "name" field when reason is empty
        create_call = client.execute_kw.call_args_list[1]
        values = create_call[0][2][0]
        assert "name" not in values

    async def test_request_leave_no_employee(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],
        ]
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="2025-07-05",
        )
        assert "No employee record found" in result["error"]

    async def test_request_leave_not_authenticated(self, unauth_tools):
        result = await unauth_tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="2025-07-05",
        )
        assert result["error"] == "Not authenticated"


# ── get_my_profile tests ────────────────────────────────────────


class TestGetMyProfile:
    async def test_returns_profile(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [
                {
                    "name": "John Doe",
                    "job_id": [1, "Developer"],
                    "department_id": [2, "Engineering"],
                    "work_email": "john@example.com",
                    "work_phone": "+1234567890",
                    "parent_id": False,
                    "coach_id": False,
                    "work_location_id": False,
                },
            ],
        ]
        result = await tools["get_my_profile"]()
        assert result["profile"]["name"] == "John Doe"
        assert result["profile"]["work_email"] == "john@example.com"

    async def test_no_record(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],
        ]
        result = await tools["get_my_profile"]()
        assert "No employee profile found" in result["error"]

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_my_profile"]()
        assert result["error"] == "Not authenticated"


# ── next_month helper tests ────────────────────────────────────


class TestNextMonth:
    def test_regular_month(self):
        assert next_month("2025-03") == "2025-04-01 00:00:00"

    def test_december_wraps_to_next_year(self):
        assert next_month("2025-12") == "2026-01-01 00:00:00"

    def test_january(self):
        assert next_month("2025-01") == "2025-02-01 00:00:00"

    def test_single_digit_month_padded(self):
        result = next_month("2025-09")
        assert result == "2025-10-01 00:00:00"


# ── Restriction enforcement tests ───────────────────────────────


@pytest.fixture
def restricted_context():
    """Create a mock context where restriction checks return a string (blocked)."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(uid=42, is_admin=False, groups=["base.group_user"])
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    # Disable security_gate sub-checks so tests focus on restriction logic
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    # Make restriction checks return a string (blocked message)
    ctx.restrictions.check_model_access.return_value = (
        "Access denied: model blocked by restriction"
    )
    return ctx, client


@pytest.fixture
def restricted_tools(restricted_context):
    """Register HR plugin with restricted context."""
    ctx, _ = restricted_context
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = HRPlugin()
    plugin.register(server, ctx)
    return captured


class TestHRRestrictionEnforcement:
    """Verify that HR plugin tools properly check restriction results.

    The plugins use ``isinstance(restriction_msg, str)`` to detect
    denied access.  These tests ensure that when the restriction
    checker returns a string, tools return an error instead of
    proceeding with the Odoo RPC call.
    """

    async def test_check_in_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["check_in"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_check_out_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["check_out"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_get_my_attendance_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["get_my_attendance"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_get_my_leaves_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["get_my_leaves"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_request_leave_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="2025-07-05",
        )
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_get_my_profile_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["get_my_profile"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_blocked_tools_never_call_odoo(
        self, restricted_tools, restricted_context
    ):
        """When restrictions block access, no Odoo RPC call should be made."""
        _, client = restricted_context
        await restricted_tools["check_in"]()
        client.execute_kw.assert_not_called()
