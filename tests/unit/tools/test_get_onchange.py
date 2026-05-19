"""Tests for the get_onchange tool."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security.config_loader import (
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any) -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == "get_onchange":
            return tool.fn
    raise AssertionError("get_onchange tool not found")


class TestGetOnchange:
    async def test_returns_onchange_values(self) -> None:
        onchange_result = {
            "value": {"amount_total": 100.0},
            "warning": None,
            "domain": {},
        }
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert "changes" in resp
        assert resp["changes"] == {"amount_total": 100.0}
        assert resp["warnings"] is None
        assert resp["model"] == "sale.order"

    async def test_not_authenticated(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert "error" in resp

    async def test_blocked_model(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="ir.config_parameter",
            values={"key": "test"},
            changed_field="key",
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_invalid_changed_field_name(self) -> None:
        gateway = make_gateway()

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="bad field!",
        )

        assert "error" in resp
        assert "Invalid field name" in resp["error"]

    async def test_invalid_values_field_name(self) -> None:
        gateway = make_gateway()

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"bad field!": 1},
            changed_field="partner_id",
        )

        assert "error" in resp
        assert "Invalid field name" in resp["error"]

    async def test_calls_onchange_method(self) -> None:
        onchange_result = {"value": {}, "warning": None, "domain": {}}
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            values={"partner_id": 1, "pricelist_id": 2},
            changed_field="partner_id",
        )

        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "sale.order"
        assert call_args[1] == "onchange"
        # args: [[], values, [changed_field], field_onchange]
        assert call_args[2][0] == []  # empty ids
        assert call_args[2][1] == {"partner_id": 1, "pricelist_id": 2}
        assert call_args[2][2] == ["partner_id"]
        # field_onchange should include all value keys
        assert "partner_id" in call_args[2][3]
        assert "pricelist_id" in call_args[2][3]

    async def test_changed_field_added_to_spec(self) -> None:
        """changed_field is added to field_onchange even if not in values keys."""
        onchange_result = {"value": {}, "warning": None, "domain": {}}
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            values={"pricelist_id": 2},
            changed_field="partner_id",
        )

        call_args = mock_client.execute_kw.call_args[0]
        field_onchange = call_args[2][3]
        assert "partner_id" in field_onchange

    async def test_with_explicit_fields(self) -> None:
        """When fields are provided, they are used for the onchange spec."""
        onchange_result = {
            "value": {"amount_total": 50.0},
            "warning": None,
            "domain": {},
        }
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
            fields=["amount_total", "currency_id"],
        )

        call_args = mock_client.execute_kw.call_args[0]
        field_onchange = call_args[2][3]
        assert "amount_total" in field_onchange
        assert "currency_id" in field_onchange
        assert "partner_id" in field_onchange

    async def test_invalid_fields_param_rejected(self) -> None:
        """fields parameter with invalid field names is rejected."""
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
            fields=["partner_id.name"],  # dot is invalid
        )
        assert "error" in resp
        assert "Invalid field" in resp["error"]
        mock_client.execute_kw.assert_not_called()

    async def test_invalid_fields_param_path_traversal(self) -> None:
        """fields parameter with path-traversal-like values is rejected."""
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
            fields=["../foo"],
        )
        assert "error" in resp
        assert "Invalid field" in resp["error"]

    async def test_returns_warnings(self) -> None:
        onchange_result = {
            "value": {},
            "warning": {"title": "Warning", "message": "Partner has overdue invoices"},
            "domain": {},
        }
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert resp["warnings"] == {
            "title": "Warning",
            "message": "Partner has overdue invoices",
        }

    async def test_api_status_ok_when_changes_present(self) -> None:
        """When onchange returns real changes, _api_status is 'ok'."""
        onchange_result = {
            "value": {"amount_total": 100.0},
            "warning": None,
            "domain": {},
        }
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert resp["_api_status"] == "ok"
        # No hint on the happy path
        assert "_api_hint" not in resp

    async def test_api_status_no_changes_for_empty_value(self) -> None:
        """Empty value + no warning is flagged as no_changes_or_unsupported."""
        onchange_result = {"value": {}, "warning": None, "domain": {}}
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert resp["_api_status"] == "no_changes_or_unsupported"
        assert "_api_hint" in resp
        assert "computed fields" in resp["_api_hint"]

    async def test_api_status_no_changes_for_empty_dict(self) -> None:
        """Bare empty dict result is also flagged as no_changes_or_unsupported."""
        mock_client = make_mock_client(execute_kw_return={})
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert resp["_api_status"] == "no_changes_or_unsupported"
        assert "_api_hint" in resp

    async def test_api_status_ok_when_warning_only(self) -> None:
        """Warning present but no value changes is still 'ok' (real signal)."""
        onchange_result = {
            "value": {},
            "warning": {"title": "Heads up", "message": "Check pricing"},
            "domain": {},
        }
        mock_client = make_mock_client(execute_kw_return=onchange_result)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )

        assert resp["_api_status"] == "ok"
