"""Tests for the Helpdesk domain plugin."""

from __future__ import annotations

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
