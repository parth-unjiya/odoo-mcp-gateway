"""Tests for the Sales domain plugin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.plugins.core.helpers import next_month
from odoo_mcp_gateway.plugins.core.sales import SalesPlugin

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
    """Register Sales plugin and capture all tool functions."""
    ctx, _ = mock_context
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = SalesPlugin()
    plugin.register(server, ctx)
    return captured


@pytest.fixture
def unauth_tools(unauth_context):
    """Register Sales plugin with unauthenticated context."""
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = SalesPlugin()
    plugin.register(server, unauth_context)
    return captured


# ── Plugin metadata ──────────────────────────────────────────────


class TestSalesPluginMetadata:
    def test_name(self):
        plugin = SalesPlugin()
        assert plugin.name == "sales"

    def test_required_odoo_modules(self):
        plugin = SalesPlugin()
        assert "sale" in plugin.required_odoo_modules

    def test_required_models(self):
        plugin = SalesPlugin()
        assert "sale.order" in plugin.required_models
        assert "sale.order.line" in plugin.required_models


# ── get_my_quotations tests ──────────────────────────────────────


class TestGetMyQuotations:
    async def test_returns_orders(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = [
            {
                "name": "S00001",
                "partner_id": [1, "Customer A"],
                "date_order": "2025-03-01",
                "amount_total": 1500.0,
                "state": "draft",
                "currency_id": [1, "USD"],
            },
        ]
        result = await tools["get_my_quotations"]()
        assert result["count"] == 1
        assert result["orders"][0]["name"] == "S00001"

    async def test_with_state_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_my_quotations"](state="sale")
        assert result["count"] == 0
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["state", "=", "sale"] in domain

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_my_quotations"]()
        assert result["error"] == "Not authenticated"

    async def test_handles_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("RPC timeout")
        result = await tools["get_my_quotations"]()
        assert "RPC timeout" in result["error"]

    async def test_limit_capped(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        await tools["get_my_quotations"](limit=200)
        call_args = client.execute_kw.call_args
        kwargs = call_args[0][3]
        assert kwargs["limit"] == 100


# ── get_order_details tests ──────────────────────────────────────


class TestGetOrderDetails:
    async def test_with_lines(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            # order search
            [
                {
                    "id": 1,
                    "name": "S00001",
                    "partner_id": [1, "Customer A"],
                    "date_order": "2025-03-01",
                    "amount_untaxed": 1000.0,
                    "amount_tax": 150.0,
                    "amount_total": 1150.0,
                    "state": "sale",
                    "currency_id": [1, "USD"],
                    "user_id": [42, "Admin"],
                    "note": "",
                },
            ],
            # order lines
            [
                {
                    "product_id": [10, "Widget"],
                    "name": "Widget - Premium",
                    "product_uom_qty": 5.0,
                    "price_unit": 200.0,
                    "discount": 0.0,
                    "price_subtotal": 1000.0,
                },
            ],
        ]
        result = await tools["get_order_details"](order_id=1)
        assert result["order"]["name"] == "S00001"
        assert result["line_count"] == 1
        assert result["lines"][0]["product_id"] == [10, "Widget"]

    async def test_order_not_found(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],  # no order
        ]
        result = await tools["get_order_details"](order_id=999)
        assert result["error"] == "Order not found"

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_order_details"](order_id=1)
        assert result["error"] == "Not authenticated"


# ── confirm_order tests ──────────────────────────────────────────


class TestConfirmOrder:
    async def test_success(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "S00001", "state": "draft"}],
            True,  # action_confirm
        ]
        result = await tools["confirm_order"](order_id=1)
        assert result["status"] == "confirmed"
        assert result["order_id"] == 1
        assert result["order_name"] == "S00001"
        assert result["previous_state"] == "draft"

    async def test_confirm_sent_order(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 2, "name": "S00002", "state": "sent"}],
            True,
        ]
        result = await tools["confirm_order"](order_id=2)
        assert result["status"] == "confirmed"
        assert result["previous_state"] == "sent"

    async def test_not_draft(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [{"id": 1, "name": "S00001", "state": "sale"}],
        ]
        result = await tools["confirm_order"](order_id=1)
        assert "error" in result
        assert "sale" in result["error"]

    async def test_order_not_found(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = [
            [],
        ]
        result = await tools["confirm_order"](order_id=999)
        assert result["error"] == "Order not found"

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["confirm_order"](order_id=1)
        assert result["error"] == "Not authenticated"


# ── get_sales_summary tests ──────────────────────────────────────


class TestGetSalesSummary:
    async def test_returns_stats(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = [
            {
                "state": "sale",
                "amount_total": 1000.0,
                "partner_id": [1, "Alpha Corp"],
                "date_order": "2025-03-01",
            },
            {
                "state": "sale",
                "amount_total": 500.0,
                "partner_id": [2, "Beta Inc"],
                "date_order": "2025-03-15",
            },
            {
                "state": "draft",
                "amount_total": 200.0,
                "partner_id": [1, "Alpha Corp"],
                "date_order": "2025-03-20",
            },
        ]
        result = await tools["get_sales_summary"]()
        assert result["total_orders"] == 3
        assert result["total_amount"] == 1700.0
        assert result["by_state"]["sale"]["count"] == 2
        assert result["by_state"]["sale"]["total"] == 1500.0
        assert result["by_state"]["draft"]["count"] == 1
        # Top customers: Alpha Corp should be first (1200 total)
        assert result["top_customers"][0]["name"] == "Alpha Corp"
        assert result["top_customers"][0]["total"] == 1200.0

    async def test_with_period_filter(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = []
        result = await tools["get_sales_summary"](period="2025-06")
        assert result["total_orders"] == 0
        call_args = client.execute_kw.call_args
        domain = call_args[0][2][0]
        assert ["date_order", ">=", "2025-06-01 00:00:00"] in domain
        assert ["date_order", "<", "2025-07-01 00:00:00"] in domain

    async def test_not_authenticated(self, unauth_tools):
        result = await unauth_tools["get_sales_summary"]()
        assert result["error"] == "Not authenticated"

    async def test_handles_exception(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.side_effect = Exception("DB error")
        result = await tools["get_sales_summary"]()
        assert "DB error" in result["error"]

    async def test_handles_none_amount(self, tools, mock_context):
        _, client = mock_context
        client.execute_kw.return_value = [
            {
                "state": "draft",
                "amount_total": None,
                "partner_id": [1, "Test"],
                "date_order": "2025-01-01",
            },
        ]
        result = await tools["get_sales_summary"]()
        assert result["total_amount"] == 0.0
        assert result["by_state"]["draft"]["total"] == 0.0


# ── next_month helper tests ────────────────────────────────────


class TestNextMonth:
    def test_regular_month(self):
        assert next_month("2025-06") == "2025-07-01 00:00:00"

    def test_december(self):
        assert next_month("2025-12") == "2026-01-01 00:00:00"


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
    """Register Sales plugin with restricted context."""
    ctx, _ = restricted_context
    server = MagicMock()
    captured: dict = {}

    def fake_tool():
        def decorator(func):
            captured[func.__name__] = func
            return func

        return decorator

    server.tool = fake_tool
    plugin = SalesPlugin()
    plugin.register(server, ctx)
    return captured


class TestSalesRestrictionEnforcement:
    """Verify that Sales plugin tools properly check restriction results.

    The plugins use ``isinstance(restriction_msg, str)`` to detect
    denied access.  These tests ensure that when the restriction
    checker returns a string, tools return an error instead of
    proceeding with the Odoo RPC call.
    """

    async def test_get_my_quotations_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["get_my_quotations"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_get_order_details_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["get_order_details"](order_id=1)
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_confirm_order_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["confirm_order"](order_id=1)
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_get_sales_summary_blocked_returns_error(
        self, restricted_tools, restricted_context
    ):
        result = await restricted_tools["get_sales_summary"]()
        assert "error" in result
        err = result["error"].lower()
        assert "denied" in err or "blocked" in err

    async def test_blocked_tools_never_call_odoo(
        self, restricted_tools, restricted_context
    ):
        """When restrictions block access, no Odoo RPC call should be made."""
        _, client = restricted_context
        await restricted_tools["get_my_quotations"]()
        client.execute_kw.assert_not_called()

    async def test_confirm_order_write_restriction_blocks(self, restricted_context):
        """confirm_order checks write access -- verify it is blocked."""
        ctx, client = restricted_context

        # Only block write, allow read
        def selective_check(model, operation, is_admin):
            if operation == "write":
                return "Write access denied"
            return None

        ctx.restrictions.check_model_access.side_effect = selective_check

        server = MagicMock()
        captured: dict = {}

        def fake_tool():
            def decorator(func):
                captured[func.__name__] = func
                return func

            return decorator

        server.tool = fake_tool
        plugin = SalesPlugin()
        plugin.register(server, ctx)

        result = await captured["confirm_order"](order_id=1)
        assert "error" in result
        assert "denied" in result["error"].lower()
