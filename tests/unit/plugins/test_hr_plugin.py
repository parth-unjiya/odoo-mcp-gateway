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
    ctx.restrictions.check_field_write.return_value = None
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
        # Call order:
        #   1) ``fields_get`` for the live state-whitelist probe
        #      (get_valid_states) — added so the plugin tracks v17/v18/v19
        #      selection differences instead of relying solely on a static
        #      frozen-set;
        #   2) employee lookup;
        #   3) hr.leave search_read.
        client.execute_kw.side_effect = [
            {"state": {"selection": [["draft", "To Submit"]]}},
            [{"id": 1}],
            [],
        ]
        result = await tools["get_my_leaves"](state="draft")
        assert result["count"] == 0
        # Verify state domain was added on the last (search_read) call
        call_args = client.execute_kw.call_args_list[-1]
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
        # Call order:
        #   1) employee lookup;
        #   2) hr.leave.type read (probes request_unit for the new
        #      hour-coercion warning);
        #   3) hr.leave create.
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [{"id": 1, "request_unit": "day"}],
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
        # Day-unit leave type means no coercion warning.
        assert "warning" not in result

        # Verify create was called with reason — the create is the LAST
        # call after the leave-type probe.
        create_call = client.execute_kw.call_args_list[-1]
        values = create_call[0][2][0]
        assert values["name"] == "Summer holiday"

    async def test_request_leave_no_reason(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [{"id": 2, "request_unit": "day"}],
            100,
        ]
        result = await tools["request_leave"](
            leave_type_id=2,
            date_from="2025-08-01",
            date_to="2025-08-02",
        )
        assert result["status"] == "created"
        # Verify no "name" field when reason is empty — create is the
        # last call after the leave-type probe.
        create_call = client.execute_kw.call_args_list[-1]
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

    async def test_request_leave_hour_unit_includes_warning(self, tools, mock_context):
        """When the leave type uses an hour-based request_unit, Odoo
        silently coerces full-day datetimes to today's working hours.
        The tool surfaces a warning so the caller knows to re-verify
        the saved record. Tracked as A13.
        """
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [{"id": 5, "request_unit": "hour"}],
            123,
        ]
        result = await tools["request_leave"](
            leave_type_id=5,
            date_from="2025-09-01",
            date_to="2025-09-01",
        )

        assert result["status"] == "created"
        assert "warning" in result
        assert "hour" in result["warning"]
        assert "Verify" in result["warning"]

    async def test_request_leave_half_day_includes_warning(self, tools, mock_context):
        """Half-day request_unit is also non-day — same warning fires."""
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            [{"id": 6, "request_unit": "half_day"}],
            124,
        ]
        result = await tools["request_leave"](
            leave_type_id=6,
            date_from="2025-09-02",
            date_to="2025-09-02",
        )

        assert "warning" in result
        assert "half_day" in result["warning"]

    async def test_request_leave_probe_failure_swallowed(self, tools, mock_context):
        """If the leave-type probe errors out the create still proceeds —
        the warning is purely informational, not a blocker.
        """
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "John"}],
            RuntimeError("network blip"),
            125,
        ]
        result = await tools["request_leave"](
            leave_type_id=7,
            date_from="2025-09-03",
            date_to="2025-09-03",
        )

        assert result["status"] == "created"
        assert result["leave_id"] == 125
        # No warning when probe failed — we can't tell what the unit is.
        assert "warning" not in result


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
                    # UAT v0.3.3 LOW-2 follow-up: user_id is now required
                    # in the projection so the resolver can defensively
                    # confirm the row belongs to the caller (uid=42 in
                    # the fixture). Without this, the row is rejected
                    # as a mismatch.
                    "user_id": [42, "Caller"],
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
        # UAT LOW-2 (Odoo 19): hint guides non-HR-linked users.
        assert "hint" in result
        assert "hr.employee" in result["hint"]

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_my_profile"]()
        assert result["error"] == "Not authenticated"

    async def test_helpdesk_manager_does_not_get_portal_message(self):
        """UAT v0.3.3 MED-5 (Odoo 19) — a helpdesk_manager whose
        ``_fetch_groups`` did not surface portal-typed XML IDs MUST
        NOT be classified as a portal user. Previously, an internal
        user with non-portal display-name groups was wrongly tripped
        into the portal short-circuit and saw
        ``"Profile not available for portal users"`` instead of the
        canonical no-employee-found shape.
        """
        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        helpdesk_auth = AuthResult(
            uid=20,
            session_id="sess",
            user_context={},
            is_admin=False,
            # Empty XML IDs (simulating transient _fetch_groups glitch
            # OR a fork that doesn't surface XML IDs) but populated
            # display-name groups for a clearly-internal user.
            groups=["Helpdesk / Manager", "User types / Internal User"],
            username="helpdesk_manager",
            database="db",
            group_xml_ids=[],
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = helpdesk_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None
        ctx.restrictions.check_model_access.return_value = None

        # No hr.employee linked → empty result from search_read.
        client.execute_kw.return_value = []

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # MUST NOT be the portal short-circuit response.
        assert "portal" not in (result.get("error") or "").lower()
        # SHOULD be the canonical no-employee-found shape.
        assert "No employee profile found" in result["error"]
        assert "hr.employee" in result.get("hint", "")

    async def test_employee_search_uses_user_id_filter(self):
        """UAT v0.3.3 MED-5 issue 1: regression to ensure the employee
        resolver filters by ``user_id`` strictly. A previous misroute
        (admin's employee returned for a non-admin user) would have
        used ``[]`` or ``[("active","=",True)]`` and returned the first
        record. This test asserts the domain Odoo sees contains the
        ``("user_id","=",uid)`` leaf with the CALLER's uid.
        """
        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        employee_auth = AuthResult(
            uid=8,
            session_id="sess",
            user_context={},
            is_admin=False,
            groups=["base.group_user"],
            username="hr_employee",
            database="db",
            group_xml_ids=["base.group_user"],
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = employee_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None
        ctx.restrictions.check_model_access.return_value = None
        ctx.rbac.filter_response_fields.side_effect = lambda r, *_a, **_k: r

        # Return a record whose id is NOT 1 (admin's id) — proving
        # the resolver filtered on user_id.
        client.execute_kw.return_value = [
            {
                "id": 17,
                "name": "Worker Bee",
                "job_id": False,
                "department_id": False,
                "work_email": "worker@example.com",
                "work_phone": False,
                "parent_id": False,
                "coach_id": False,
                "work_location_id": False,
                # UAT v0.3.3 LOW-2 follow-up: user_id must match caller.
                "user_id": [8, "hr_employee"],
            }
        ]

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # The returned profile must be the worker's record (not admin's).
        assert result["profile"]["id"] == 17
        assert result["profile"]["name"] == "Worker Bee"
        # Sanity: client was called with the user_id filter.
        # search_read invocation: execute_kw("hr.employee", "search_read",
        # [[["user_id","=",8]]], {...})
        call_args = client.execute_kw.call_args
        assert call_args[0][0] == "hr.employee"
        assert call_args[0][1] == "search_read"
        domain = call_args[0][2][0]
        assert ["user_id", "=", 8] in domain

    async def test_portal_user_friendly_error_no_internal_leakage(self):
        """UAT LOW-1 (Odoo 19) — portal users get a friendly error.

        The previous response leaked ``hr.employee.public`` and the
        missing-group XML ID. Both are minor information disclosure
        for an external caller. The portal-detection short-circuit
        replaces the response with stock-strings only — no internal
        model names, no group identifiers, no Odoo internals.
        """
        from unittest.mock import AsyncMock, MagicMock

        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        portal_auth = AuthResult(
            uid=9,
            session_id="sess",
            user_context={},
            is_admin=False,
            groups=[],
            username="portal_test",
            database="db",
            group_xml_ids=["base.group_portal"],
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = portal_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # Portal-friendly response.
        assert "Profile not available for portal users" in result["error"]
        assert "hint" in result
        # MUST NOT leak internal model name or group XML ID.
        full = (result["error"] + " " + result.get("hint", "")).lower()
        assert "hr.employee.public" not in full
        assert "group_portal" not in full
        assert "role / member" not in full

    # ── UAT v0.3.3 LOW-2 follow-up (Finding #5c) ──────────────────
    #
    # ``sales_user`` (uid 5 on Odoo 19) has NO ``hr.employee``
    # record. Re-UAT showed ``get_my_profile`` returning admin's
    # employee record (id=1, "Administrator") for this caller.
    # Two regressions are covered here:
    #
    # 1. Empty search_read result MUST yield the friendly
    #    "No employee profile found" hint — never silently fall
    #    back to any other row.
    # 2. If Odoo returns a row whose ``user_id`` does NOT match
    #    the caller's uid (race / ir.rule fallthrough / custom
    #    override), the resolver MUST reject it as "no employee
    #    found" rather than leak another user's record.

    async def test_unlinked_user_no_admin_fallback(self):
        """Finding #5c: ``sales_user``-shaped caller (internal-user
        groups, no linked hr.employee) must receive the friendly
        no-employee hint when ``search_read`` returns an empty list —
        NEVER admin's record by accident.
        """
        from unittest.mock import AsyncMock, MagicMock

        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        sales_auth = AuthResult(
            uid=5,
            session_id="sess",
            user_context={},
            is_admin=False,
            groups=["base.group_user", "sales_team.group_sale_salesman"],
            username="sales_user",
            database="db",
            group_xml_ids=[
                "base.group_user",
                "sales_team.group_sale_salesman",
            ],
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = sales_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None
        ctx.restrictions.check_model_access.return_value = None

        # The CORRECT live response for an unlinked user: empty list.
        client.execute_kw.return_value = []

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # MUST be the friendly hint, NOT admin's profile.
        assert "profile" not in result
        assert "No employee profile found" in result["error"]
        assert "hr.employee" in result.get("hint", "")
        # Sanity: the search domain was strictly user_id=5.
        call_args = client.execute_kw.call_args
        assert call_args[0][0] == "hr.employee"
        assert call_args[0][1] == "search_read"
        domain = call_args[0][2][0]
        assert ["user_id", "=", 5] in domain

    async def test_user_id_mismatch_rejected(self):
        """Finding #5c defense-in-depth: if Odoo returns a row whose
        ``user_id`` does NOT match the caller's uid (e.g. ir.rule
        fallthrough surfacing admin's record), the resolver MUST
        reject the row and return the no-employee hint rather than
        leak the mismatched profile.
        """
        from unittest.mock import AsyncMock, MagicMock

        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        sales_auth = AuthResult(
            uid=5,
            session_id="sess",
            user_context={},
            is_admin=False,
            groups=["base.group_user"],
            username="sales_user",
            database="db",
            group_xml_ids=["base.group_user"],
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = sales_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None
        ctx.restrictions.check_model_access.return_value = None
        ctx.rbac.filter_response_fields.side_effect = lambda r, *_a, **_k: r

        # Pathological: Odoo returned admin's hr.employee record even
        # though we filtered by user_id=5. Caller MUST NOT see this.
        client.execute_kw.return_value = [
            {
                "id": 1,
                "name": "Administrator",
                "job_id": False,
                "department_id": [1, "Administration"],
                "work_email": "admin@example.com",
                "work_phone": False,
                "parent_id": False,
                "coach_id": False,
                "work_location_id": False,
                "user_id": [2, "admin"],  # NOT the caller's uid (5).
            }
        ]

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # Mismatched row must NOT leak through.
        assert "profile" not in result
        assert "Administrator" not in str(result)
        assert "No employee profile found" in result["error"]
        assert "hr.employee" in result.get("hint", "")

    # ── UAT v0.3.3 LOW-1 follow-up (Finding #5d, REGRESSION) ──────
    #
    # ``portal_test`` on Odoo 19 produced an EMPTY ``auth_result``
    # (groups=[], group_xml_ids=[]) because of a glitch in the
    # group-resolution path. With the tightened ``is_portal_user``
    # (a deliberate fix for helpdesk_manager misclassification),
    # an empty-group auth_result is no longer treated as portal,
    # so the code fell through to read hr.employee → Odoo raised
    # raw ACL error leaking ``hr.employee.public`` and
    # ``Role / Member``. These tests pin the friendly-error path.

    async def test_empty_group_caller_treated_as_portal(self):
        """Finding #5d: a caller whose auth_result has BOTH empty
        ``groups`` AND empty ``group_xml_ids`` must receive the
        friendly portal response (not a raw ACL leak).
        """
        from unittest.mock import AsyncMock, MagicMock

        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        empty_auth = AuthResult(
            uid=9,
            session_id="sess",
            user_context={},
            is_admin=False,
            groups=[],
            username="portal_test",
            database="db",
            group_xml_ids=[],  # NB: empty — the regression trigger.
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = empty_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None
        ctx.restrictions.check_model_access.return_value = None

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # Friendly portal response, NO leaked Odoo internals.
        assert "Profile not available for portal users" in result["error"]
        assert "hint" in result
        full = (result["error"] + " " + result.get("hint", "")).lower()
        assert "hr.employee" not in full
        assert "public employee" not in full
        assert "role" not in full
        # The Odoo read MUST NOT even have been attempted.
        client.execute_kw.assert_not_called()

    async def test_acl_leak_caught_in_exception_handler(self):
        """Finding #5d defence-in-depth: if a caller somehow slips
        past the early portal gate (e.g. has ``base.group_user`` set
        but Odoo still denies ``hr.employee.public`` for some reason)
        and Odoo raises an ACL error mentioning the internal model
        name, the exception handler MUST convert it to the friendly
        portal message before it reaches the wire.
        """
        from unittest.mock import AsyncMock, MagicMock

        from odoo_mcp_gateway.client.base import AuthResult
        from odoo_mcp_gateway.plugins.core.hr import HRPlugin

        weird_auth = AuthResult(
            uid=99,
            session_id="sess",
            user_context={},
            is_admin=False,
            # Has an internal-user-looking group so the early
            # broader-portal gate does NOT fire — we want to
            # exercise the inner except clause.
            groups=["base.group_user"],
            username="weird_user",
            database="db",
            group_xml_ids=["base.group_user"],
        )
        ctx = MagicMock()
        client = AsyncMock()
        auth_mgr = MagicMock()
        auth_mgr.get_active_client.return_value = client
        auth_mgr.auth_result = weird_auth
        ctx.auth_managers = {"session": auth_mgr}
        ctx.sanitize_error = lambda exc: str(exc)
        ctx.rate_limiter = None
        ctx.audit_logger = None
        ctx.rbac.check_tool_access.return_value = None
        ctx.restrictions.check_field_write.return_value = None
        ctx.restrictions.check_model_access.return_value = None

        # Verbatim Odoo 19 raw ACL message that leaked in re-UAT.
        client.execute_kw.side_effect = Exception(
            "Access denied: You are not allowed to access "
            "'Public Employee' (hr.employee.public) records.\n\n"
            "This operation is allowed for the following groups:\n"
            "\t- Role / Member\n\nContact your administrator to "
            "request access if necessary."
        )

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HRPlugin().register(server, ctx)

        result = await captured["get_my_profile"]()
        # Converted to friendly response — NO raw model name leaked.
        assert "Profile not available for portal users" in result["error"]
        full = (result["error"] + " " + result.get("hint", "")).lower()
        assert "hr.employee.public" not in full
        assert "public employee" not in full
        assert "role / member" not in full


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


# ── Security gate failure tests ──────────────────────────────────


@pytest.fixture
def gate_blocked_context():
    """Create a mock context where security_gate returns an error."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(uid=42)
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    # Make rate_limiter block all calls
    ctx.rate_limiter = MagicMock()
    ctx.rate_limiter.check.return_value = (False, "Rate limit exceeded")
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    return ctx, client


@pytest.fixture
def gate_blocked_tools(gate_blocked_context):
    """Register HR plugin with gate-blocked context."""
    ctx, _ = gate_blocked_context
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


class TestSecurityGateBlocked:
    """Verify that tools return error when security_gate blocks them."""

    async def test_check_in_gate_blocked(
        self, gate_blocked_tools, gate_blocked_context
    ):
        result = await gate_blocked_tools["check_in"]()
        assert "error" in result
        assert "Rate limit" in result["error"]

    async def test_check_out_gate_blocked(
        self, gate_blocked_tools, gate_blocked_context
    ):
        result = await gate_blocked_tools["check_out"]()
        assert "error" in result
        assert "Rate limit" in result["error"]

    async def test_get_my_attendance_gate_blocked(
        self, gate_blocked_tools, gate_blocked_context
    ):
        result = await gate_blocked_tools["get_my_attendance"]()
        assert "error" in result
        assert "Rate limit" in result["error"]

    async def test_get_my_leaves_gate_blocked(
        self, gate_blocked_tools, gate_blocked_context
    ):
        result = await gate_blocked_tools["get_my_leaves"]()
        assert "error" in result
        assert "Rate limit" in result["error"]

    async def test_request_leave_gate_blocked(
        self, gate_blocked_tools, gate_blocked_context
    ):
        result = await gate_blocked_tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="2025-07-05",
        )
        assert "error" in result
        assert "Rate limit" in result["error"]

    async def test_get_my_profile_gate_blocked(
        self, gate_blocked_tools, gate_blocked_context
    ):
        result = await gate_blocked_tools["get_my_profile"]()
        assert "error" in result
        assert "Rate limit" in result["error"]

    async def test_gate_blocked_never_calls_odoo(
        self, gate_blocked_tools, gate_blocked_context
    ):
        """When gate blocks, no Odoo RPC call should happen."""
        _, client = gate_blocked_context
        await gate_blocked_tools["check_in"]()
        await gate_blocked_tools["check_out"]()
        client.execute_kw.assert_not_called()


# ── UID zero (not authenticated via uid) tests ───────────────────


@pytest.fixture
def uid_zero_context():
    """Context with auth_managers present but uid=0."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(uid=0)
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    return ctx, client


@pytest.fixture
def uid_zero_tools(uid_zero_context):
    """Register HR plugin with uid=0 context."""
    ctx, _ = uid_zero_context
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


class TestUidZeroAuth:
    """Tools should return 'Not authenticated' when uid is 0."""

    async def test_check_in_uid_zero(self, uid_zero_tools):
        result = await uid_zero_tools["check_in"]()
        assert result["error"] == "Not authenticated"

    async def test_check_out_uid_zero(self, uid_zero_tools):
        result = await uid_zero_tools["check_out"]()
        assert result["error"] == "Not authenticated"

    async def test_get_my_attendance_uid_zero(self, uid_zero_tools):
        result = await uid_zero_tools["get_my_attendance"]()
        assert result["error"] == "Not authenticated"

    async def test_get_my_leaves_uid_zero(self, uid_zero_tools):
        result = await uid_zero_tools["get_my_leaves"]()
        assert result["error"] == "Not authenticated"

    async def test_get_my_profile_uid_zero(self, uid_zero_tools):
        result = await uid_zero_tools["get_my_profile"]()
        assert result["error"] == "Not authenticated"


# ── Input validation edge cases ──────────────────────────────────


class TestInputValidation:
    async def test_invalid_month_format(self, tools, mock_context):
        """get_my_attendance rejects invalid month format."""
        result = await tools["get_my_attendance"](month="2025/03")
        assert "error" in result
        assert "Invalid month format" in result["error"]

    async def test_invalid_leave_state(self, tools, mock_context):
        """get_my_leaves rejects invalid state values."""
        result = await tools["get_my_leaves"](state="invalid_state")
        assert "error" in result
        assert "Invalid state" in result["error"]

    async def test_negative_leave_type_id(self, tools, mock_context):
        """request_leave rejects non-positive leave_type_id."""
        result = await tools["request_leave"](
            leave_type_id=0,
            date_from="2025-07-01",
            date_to="2025-07-05",
        )
        assert "error" in result
        assert "positive integer" in result["error"]

    async def test_invalid_date_from_format(self, tools, mock_context):
        """request_leave rejects invalid date_from format."""
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="07-01-2025",
            date_to="2025-07-05",
        )
        assert "error" in result
        assert "date_from" in result["error"]

    async def test_invalid_date_to_format(self, tools, mock_context):
        """request_leave rejects invalid date_to format."""
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="not-a-date",
        )
        assert "error" in result
        assert "date_to" in result["error"]

    async def test_date_from_after_date_to(self, tools, mock_context):
        """request_leave rejects date_from > date_to."""
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-10",
            date_to="2025-07-05",
        )
        assert "error" in result
        assert "date_from must not be after date_to" in result["error"]

    async def test_invalid_calendar_date(self, tools, mock_context):
        """request_leave rejects dates that match regex but are invalid calendars."""
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="2025-02-30",
            date_to="2025-02-30",
        )
        assert "error" in result
        assert "Invalid calendar date" in result["error"]

    async def test_limit_minimum_is_one(self, tools, mock_context):
        """get_my_attendance with limit=0 should use limit=1."""
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1}],
            [],
        ]
        await tools["get_my_attendance"](limit=0)
        call_args = client.execute_kw.call_args_list[1]
        kwargs = call_args[0][3]
        assert kwargs["limit"] == 1


# ── Exception handling edge cases ────────────────────────────────


class TestExceptionHandling:
    async def test_check_out_handles_exception(self, tools, mock_context):
        """check_out exception is caught and sanitized."""
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Network timeout")
        result = await tools["check_out"]()
        assert "error" in result
        assert "Network timeout" in result["error"]

    async def test_get_my_attendance_handles_exception(self, tools, mock_context):
        """get_my_attendance exception is caught and sanitized."""
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Server down")
        result = await tools["get_my_attendance"]()
        assert "error" in result
        assert "Server down" in result["error"]

    async def test_get_my_leaves_handles_exception(self, tools, mock_context):
        """get_my_leaves exception is caught and sanitized."""
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Connection reset")
        result = await tools["get_my_leaves"]()
        assert "error" in result
        assert "Connection reset" in result["error"]

    async def test_request_leave_handles_exception(self, tools, mock_context):
        """request_leave exception is caught and sanitized."""
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Odoo RPC error")
        result = await tools["request_leave"](
            leave_type_id=1,
            date_from="2025-07-01",
            date_to="2025-07-05",
        )
        assert "error" in result

    async def test_get_my_profile_handles_exception(self, tools, mock_context):
        """get_my_profile exception is caught and sanitized."""
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Internal error")
        result = await tools["get_my_profile"]()
        assert "error" in result

    async def test_model_not_found_error(self, tools, mock_context):
        """Exception indicating model does not exist returns helpful message."""
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Object hr.employee does not exist")
        result = await tools["check_in"]()
        assert "error" in result
        assert "not available" in result["error"]
        assert "module may not be installed" in result["error"]


# ── Partial restriction scenarios ────────────────────────────────


@pytest.fixture
def attendance_restricted_context():
    """Context where hr.attendance is blocked but hr.employee is allowed."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(uid=42, is_admin=False, groups=["base.group_user"])
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None

    def check_model_access(model, operation, is_admin):
        if model == "hr.attendance":
            return "Access denied: hr.attendance blocked"
        return None

    ctx.restrictions.check_model_access = MagicMock(side_effect=check_model_access)
    return ctx, client


@pytest.fixture
def attendance_restricted_tools(attendance_restricted_context):
    """Register HR plugin with attendance-restricted context."""
    ctx, _ = attendance_restricted_context
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


class TestPartialRestrictions:
    """When only hr.attendance is blocked, check_in should fail at that check."""

    async def test_check_in_blocked_on_attendance_read(
        self, attendance_restricted_tools, attendance_restricted_context
    ):
        result = await attendance_restricted_tools["check_in"]()
        assert "error" in result
        assert "hr.attendance" in result["error"]

    async def test_check_out_blocked_on_attendance_read(
        self, attendance_restricted_tools, attendance_restricted_context
    ):
        result = await attendance_restricted_tools["check_out"]()
        assert "error" in result
        assert "hr.attendance" in result["error"]

    async def test_get_my_attendance_blocked_on_attendance_read(
        self, attendance_restricted_tools, attendance_restricted_context
    ):
        result = await attendance_restricted_tools["get_my_attendance"]()
        assert "error" in result
        assert "hr.attendance" in result["error"]
