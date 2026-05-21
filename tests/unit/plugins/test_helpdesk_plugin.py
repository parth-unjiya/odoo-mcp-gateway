"""Tests for the Helpdesk domain plugin."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin

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
    """Register Helpdesk plugin and capture all tool functions."""
    ctx, _ = mock_context
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = HelpdeskPlugin()
    plugin.register(server, ctx)
    return captured


@pytest.fixture
def unauth_tools(unauth_context):
    """Register Helpdesk plugin with unauthenticated context."""
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = HelpdeskPlugin()
    plugin.register(server, unauth_context)
    return captured


# ── Plugin metadata ──────────────────────────────────────────────


class TestHelpdeskPluginMetadata:
    def test_name(self):
        plugin = HelpdeskPlugin()
        assert plugin.name == "helpdesk"

    def test_required_odoo_modules(self):
        plugin = HelpdeskPlugin()
        assert "helpdesk" in plugin.required_odoo_modules

    def test_required_models(self):
        plugin = HelpdeskPlugin()
        assert "helpdesk.ticket" in plugin.required_models


# ── get_my_tickets tests ─────────────────────────────────────────


class TestGetMyTickets:
    async def test_returns_tickets(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = [
            {
                "name": "Login broken",
                "description": "Cannot log in",
                "stage_id": [1, "New"],
                "priority": "2",
                "team_id": [1, "Support"],
                "partner_id": [10, "Customer X"],
                "create_date": "2025-03-01 10:00:00",
            },
        ]
        result = await tools["get_my_tickets"]()
        assert result["count"] == 1
        assert result["tickets"][0]["name"] == "Login broken"

    async def test_with_priority_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tickets"](priority="3")
        assert result["count"] == 0
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["priority", "=", "3"] in domain

    async def test_with_state_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tickets"](state="New")
        assert result["count"] == 0
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["stage_id.name", "=", "New"] in domain

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_my_tickets"]()
        assert result["error"] == "Not authenticated"

    async def test_model_not_found_error(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception(
            "Model 'helpdesk.ticket' does not exist"
        )
        result = await tools["get_my_tickets"]()
        assert "not available" in result["error"]
        assert "ticket.helpdesk" in result["error"]

    async def test_handles_generic_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Connection refused")
        result = await tools["get_my_tickets"]()
        assert result["error"] == "Connection refused"

    async def test_opaque_unexpected_error_replaced_with_hint(
        self, mock_context
    ) -> None:
        """UAT v0.3.3 LOW (Odoo 19): when the plugin wrapper fails for
        an unspecified reason that the sanitiser collapses to the
        bare ``"An unexpected error occurred"`` string, the response
        is replaced with a friendlier shape — empty tickets list +
        a hint pointing at the supported ``search_read`` workaround.
        Previously, helpdesk_user (no hr.employee record) calling
        get_my_tickets saw the opaque message and had no way to
        diagnose / work around it.
        """
        from unittest.mock import MagicMock

        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin

        ctx, client = mock_context
        # Override sanitize_error to return the opaque sanitiser fallback —
        # the contract the plugin must softening-handle. Whatever the
        # underlying exception, the gateway-level sanitiser may strip
        # everything and emit this string for security reasons.
        ctx.sanitize_error = lambda _exc: "An unexpected error occurred"

        # Trigger an arbitrary exception in the search_read path.
        client.execute_kw.side_effect = RuntimeError("opaque internal failure")

        # Re-register the plugin on the patched context to capture the
        # updated tool definitions (the ``tools`` fixture closed over the
        # original sanitize_error).
        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HelpdeskPlugin().register(server, ctx)

        result = await captured["get_my_tickets"]()
        # MUST NOT surface the opaque message verbatim.
        assert result.get("error") != "An unexpected error occurred"
        # Wire shape: empty list + hint guiding caller to search_read.
        assert result.get("tickets") == []
        assert result.get("count") == 0
        assert "hint" in result
        assert "search_read" in result["hint"]
        # Hint references the resolved ticket model so the caller
        # knows exactly which model to query directly.
        assert "helpdesk.ticket" in result["hint"]


# ── create_ticket tests ──────────────────────────────────────────


class TestCreateTicket:
    async def test_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = 77
        result = await tools["create_ticket"](
            name="Cannot export report",
            description="Export button throws error",
            team_id=3,
            priority="2",
        )
        assert result["status"] == "created"
        assert result["ticket_id"] == 77
        assert result["name"] == "Cannot export report"
        assert result["priority"] == "2"

        # Verify create args
        call_args = client.execute_kw.call_args
        values = call_args[0][2][0]
        assert values["name"] == "Cannot export report"
        assert values["description"] == "Export button throws error"
        assert values["team_id"] == 3
        assert values["user_id"] == 42

    async def test_minimal_ticket(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = 78
        result = await tools["create_ticket"](name="Quick question")
        assert result["status"] == "created"
        # Verify no description or team_id in values
        call_args = client.execute_kw.call_args
        values = call_args[0][2][0]
        assert "description" not in values
        assert "team_id" not in values

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["create_ticket"](name="Test")
        assert result["error"] == "Not authenticated"

    async def test_model_not_found_error(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception(
            "Model 'helpdesk.ticket' does not exist"
        )
        result = await tools["create_ticket"](name="Test")
        assert "not available" in result["error"]


# ── get_my_tickets state validation tests ────────────────────────


class TestGetMyTicketsStateValidation:
    """Verify the state filter input is validated before reaching Odoo."""

    async def test_invalid_state_with_special_chars_rejected(self, tools, mock_context):
        """SQL-injection-like payloads must be rejected without hitting Odoo."""
        _, client = mock_context
        result = await tools["get_my_tickets"](state="'; DROP TABLE--")
        assert result.get("error") == "Invalid state filter format"
        # The search must never be issued
        client.execute_kw.assert_not_called()

    async def test_invalid_state_too_long_rejected(self, tools, mock_context):
        """State strings beyond 64 chars are rejected."""
        _, client = mock_context
        result = await tools["get_my_tickets"](state="a" * 100)
        assert result.get("error") == "Invalid state filter format"
        client.execute_kw.assert_not_called()

    async def test_invalid_state_empty_string_rejected(self, tools, mock_context):
        """Empty string fails the {1,64} length requirement."""
        _, client = mock_context
        result = await tools["get_my_tickets"](state="")
        assert result.get("error") == "Invalid state filter format"
        client.execute_kw.assert_not_called()

    async def test_state_none_skips_filter(self, tools, mock_context):
        """When state is None, no validation runs and no stage filter applies."""
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tickets"]()
        assert "error" not in result
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        stage_entries = [
            d for d in domain if isinstance(d, list) and d[0] == "stage_id.name"
        ]
        assert stage_entries == []

    async def test_valid_state_simple_word_accepted(self, tools, mock_context):
        """Plain stage names like 'New' pass validation and reach Odoo."""
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tickets"](state="New")
        assert "error" not in result
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["stage_id.name", "=", "New"] in domain

    async def test_valid_state_with_space_and_hyphen_accepted(
        self, tools, mock_context
    ):
        """Multi-word stage names with hyphens are accepted."""
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_tickets"](state="In Progress")
        assert "error" not in result
        result = await tools["get_my_tickets"](state="Pending-Review")
        assert "error" not in result

    async def test_invalid_state_with_unicode_quote_rejected(self, tools, mock_context):
        """Smart quotes and other non-allowed chars are rejected."""
        _, client = mock_context
        result = await tools["get_my_tickets"](state="New’")
        assert result.get("error") == "Invalid state filter format"
        client.execute_kw.assert_not_called()


# ── create_ticket user_id sanitization tests ─────────────────────


class TestCreateTicketUserIdSanitization:
    """Verify create_ticket refuses to silently drop the user_id assignment."""

    async def test_user_id_drop_returns_error(self, tools, mock_context):
        """If RBAC strips user_id from the write values, fail loudly."""
        ctx, client = mock_context

        # Simulate an RBAC policy that removes user_id from helpdesk.ticket
        # writes for this user.
        def _strip_user_id(values, model, user_groups, is_admin):
            return {k: v for k, v in values.items() if k != "user_id"}

        ctx.rbac.sanitize_write_values.side_effect = _strip_user_id

        result = await tools["create_ticket"](name="Triage me")

        assert "error" in result
        assert "user_id" in result["error"]
        assert "permission" in result["error"].lower()
        # The ticket must NOT have been created
        client.execute_kw.assert_not_called()

    async def test_user_id_preserved_allows_creation(self, tools, mock_context):
        """When RBAC keeps user_id intact, creation proceeds normally."""
        ctx, client = mock_context
        # Identity sanitizer: returns values unchanged.
        ctx.rbac.sanitize_write_values.side_effect = (
            lambda values, model, user_groups, is_admin: dict(values)
        )
        client.execute_kw.return_value = 99

        result = await tools["create_ticket"](name="OK")

        assert result.get("status") == "created"
        assert result.get("ticket_id") == 99
        call_args = client.execute_kw.call_args
        values = call_args[0][2][0]
        assert values["user_id"] == 42


# ── update_ticket_stage tests ────────────────────────────────────


class TestUpdateTicketStage:
    async def test_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 5, "name": "Login broken", "stage_id": [1, "New"]}],
            True,
        ]
        result = await tools["update_ticket_stage"](ticket_id=5, stage_id=3)
        assert result["status"] == "updated"
        assert result["ticket_id"] == 5
        assert result["ticket_name"] == "Login broken"
        assert result["old_stage"] == [1, "New"]
        assert result["new_stage_id"] == 3

    async def test_ticket_not_found(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],
        ]
        result = await tools["update_ticket_stage"](ticket_id=999, stage_id=2)
        assert result["error"] == "Ticket not found"

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["update_ticket_stage"](ticket_id=1, stage_id=2)
        assert result["error"] == "Not authenticated"

    async def test_model_not_found_error(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("Model not found: helpdesk.ticket")
        result = await tools["update_ticket_stage"](ticket_id=1, stage_id=2)
        assert "not available" in result["error"]


# ── IDOR Protection tests ──────────────────────────────────────


def _make_idor_context(*, uid: int, is_admin: bool):
    """Create a mock context with explicit admin/non-admin settings."""
    ctx = MagicMock()
    client = AsyncMock()
    auth_mgr = MagicMock()
    auth_mgr.get_active_client.return_value = client
    auth_mgr.auth_result = MagicMock(
        uid=uid,
        is_admin=is_admin,
        groups=["base.group_system" if is_admin else "base.group_user"],
    )
    ctx.auth_managers = {"session": auth_mgr}
    ctx.sanitize_error = lambda exc: str(exc)
    ctx.rate_limiter = None
    ctx.audit_logger = None
    ctx.rbac.check_tool_access.return_value = None
    ctx.restrictions.check_field_write.return_value = None
    return ctx, client


def _register_helpdesk(ctx):
    """Register Helpdesk plugin on a mock server and return captured tools."""
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = HelpdeskPlugin()
    plugin.register(server, ctx)
    return captured


@pytest.fixture
def nonadmin_helpdesk_context():
    return _make_idor_context(uid=42, is_admin=False)


@pytest.fixture
def admin_helpdesk_context():
    return _make_idor_context(uid=1, is_admin=True)


@pytest.fixture
def nonadmin_helpdesk_tools(nonadmin_helpdesk_context):
    ctx, _ = nonadmin_helpdesk_context
    return _register_helpdesk(ctx)


@pytest.fixture
def admin_helpdesk_tools(admin_helpdesk_context):
    ctx, _ = admin_helpdesk_context
    return _register_helpdesk(ctx)


class TestUpdateTicketStageAclScope:
    """UAT HIGH-1 — defer ticket visibility to Odoo's ir.rule.

    The previous implementation added ``["user_id", "=", uid]`` to the
    search_read domain for non-admin callers, which is strictly narrower
    than Odoo's row-level ACL. A helpdesk_manager who could read a ticket
    (because their group's ir.rule grants team-wide visibility) but did
    not own it got "Ticket not found" — a misleading 404 for an operation
    the role was authorised to perform. The fix removes the clamp and
    trusts Odoo's ir.rule for read visibility; subsequent write failures
    surface Odoo's own write-ACL error.
    """

    async def test_nonadmin_update_ticket_stage_no_user_id_clamp(
        self, nonadmin_helpdesk_tools, nonadmin_helpdesk_context
    ):
        """The search domain must not narrow below ``["id", "=", ticket_id]``."""
        _, client = nonadmin_helpdesk_context
        client.execute_kw.side_effect = [
            [{"id": 5, "name": "Bug report", "stage_id": [1, "New"]}],
            True,  # write
        ]
        await nonadmin_helpdesk_tools["update_ticket_stage"](ticket_id=5, stage_id=3)
        call_args = client.execute_kw.call_args_list[0]
        domain = call_args[0][2][0]
        # No ``user_id`` clamp — Odoo's ir.rule is now the sole gate.
        assert all(not (isinstance(d, list) and d[0] == "user_id") for d in domain)
        assert ["id", "=", 5] in domain

    async def test_admin_update_ticket_stage_uses_id_only_domain(
        self, admin_helpdesk_tools, admin_helpdesk_context
    ):
        """Admin behaviour unchanged — domain is still id-only."""
        _, client = admin_helpdesk_context
        client.execute_kw.side_effect = [
            [{"id": 5, "name": "Bug report", "stage_id": [1, "New"]}],
            True,
        ]
        await admin_helpdesk_tools["update_ticket_stage"](ticket_id=5, stage_id=3)
        call_args = client.execute_kw.call_args_list[0]
        domain = call_args[0][2][0]
        assert ["id", "=", 5] in domain
        assert all(not (isinstance(d, list) and d[0] == "user_id") for d in domain)

    async def test_manager_can_update_ticket_owned_by_other_user(
        self, nonadmin_helpdesk_tools, nonadmin_helpdesk_context
    ):
        """Manager (non-assignee) can transition a ticket they can read."""
        ctx, client = nonadmin_helpdesk_context
        # The non-admin manager has read access to ticket 11 via ir.rule
        # (Odoo returns the record from search_read) AND write succeeds.
        client.execute_kw.side_effect = [
            [{"id": 11, "name": "Help me", "stage_id": [1, "New"]}],
            True,  # write succeeds
        ]
        result = await nonadmin_helpdesk_tools["update_ticket_stage"](
            ticket_id=11, stage_id=2
        )
        assert result.get("status") == "updated"
        assert result.get("ticket_id") == 11
        assert result.get("new_stage_id") == 2

    async def test_write_acl_error_surfaces_verbatim_not_404(
        self, nonadmin_helpdesk_tools, nonadmin_helpdesk_context
    ):
        """If Odoo allows read but denies write, surface the write error."""
        ctx, client = nonadmin_helpdesk_context
        # Search succeeds (read ACL passes); write raises an ACL error.
        client.execute_kw.side_effect = [
            [{"id": 14, "name": "Other team ticket", "stage_id": [1, "New"]}],
            Exception("AccessError: You are not allowed to write"),
        ]
        result = await nonadmin_helpdesk_tools["update_ticket_stage"](
            ticket_id=14, stage_id=2
        )
        # Surface the Odoo-side error (post-sanitisation), not "not found".
        assert result.get("error") is not None
        assert "Ticket not found" not in result["error"]


# ── v0.3.3 follow-up MED-3: effective_model_name resolution ────────────


def _make_context_with_registry(effective_model: str) -> tuple[Any, AsyncMock]:
    """Build a helpdesk context whose plugin_registry reports a specific
    ``effective_model_name`` so the tool reads the configured custom
    module name (e.g. ``ticket.helpdesk``) instead of the stock default.
    """
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
    ctx.restrictions.check_field_write.return_value = None

    # Plugin registry returns an info object whose effective_model_name
    # matches the configured override target.
    info = MagicMock()
    info.effective_model_name = effective_model
    info.missing_modules = []
    ctx.plugin_registry.get_plugin.return_value = info
    return ctx, client


class TestEffectiveModelName:
    """Plugin tools must respect ``PluginInfo.effective_model_name``
    so an operator-configured custom module name (e.g.
    ``ticket.helpdesk``) is used in place of the stock default."""

    async def test_get_my_tickets_uses_effective_model(self) -> None:
        ctx, client = _make_context_with_registry("ticket.helpdesk")
        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HelpdeskPlugin().register(server, ctx)

        client.execute_kw.return_value = []
        await captured["get_my_tickets"]()

        # The first positional arg to execute_kw is the model name.
        call = client.execute_kw.call_args
        assert call[0][0] == "ticket.helpdesk"

    async def test_create_ticket_uses_effective_model(self) -> None:
        ctx, client = _make_context_with_registry("ticket.helpdesk")
        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HelpdeskPlugin().register(server, ctx)

        ctx.rbac.sanitize_write_values.side_effect = (
            lambda values, model, user_groups, is_admin: dict(values)
        )
        client.execute_kw.return_value = 11
        await captured["create_ticket"](name="t")

        call = client.execute_kw.call_args
        assert call[0][0] == "ticket.helpdesk"

    async def test_update_ticket_stage_uses_effective_model(self) -> None:
        ctx, client = _make_context_with_registry("ticket.helpdesk")
        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HelpdeskPlugin().register(server, ctx)

        ctx.rbac.sanitize_write_values.side_effect = (
            lambda values, model, user_groups, is_admin: dict(values)
        )
        client.execute_kw.side_effect = [
            [{"id": 5, "name": "x", "stage_id": [1, "New"]}],
            True,
        ]
        await captured["update_ticket_stage"](ticket_id=5, stage_id=3)
        # Both search_read and write use the configured custom model name.
        for call in client.execute_kw.call_args_list:
            assert call[0][0] == "ticket.helpdesk"

    async def test_no_override_falls_back_to_stock_model(self) -> None:
        """A non-string ``effective_model_name`` (or its absence) falls
        back to the plugin's class default ``helpdesk.ticket``."""
        ctx, client = _make_context_with_registry("helpdesk.ticket")
        # Now scramble: registry reports None / wrong-type, plugin must
        # fall back to the static ``ticket_model`` default.
        info = ctx.plugin_registry.get_plugin.return_value
        info.effective_model_name = None
        info.missing_modules = []

        from odoo_mcp_gateway.plugins.core.helpdesk import HelpdeskPlugin

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        HelpdeskPlugin().register(server, ctx)
        client.execute_kw.return_value = []
        await captured["get_my_tickets"]()
        assert client.execute_kw.call_args[0][0] == "helpdesk.ticket"
