"""Tests for the update_record tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAccessError, OdooValidationError
from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any) -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == "update_record":
            return tool.fn
    raise AssertionError("update_record tool not found")


class TestUpdateRecord:
    async def test_success(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"name": "Updated"},
        )

        assert resp["success"] is True
        assert resp["model"] == "res.partner"
        assert resp["id"] == 1

    async def test_read_only_model_fails(self) -> None:
        gateway = make_gateway(
            model_access_config=ModelAccessConfig(
                default_policy="deny",
                stock_models={"read_only": ["res.currency"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.currency",
            record_id=1,
            values={"name": "XYZ"},
        )

        assert "error" in resp
        assert "read-only" in resp["error"]

    async def test_blocked_model_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="ir.config_parameter",
            record_id=1,
            values={"value": "x"},
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_strips_blocked_write_fields(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                blocked_write_fields=["groups_id"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"name": "Test", "groups_id": [[6, 0, [1]]]},
        )

        assert "error" in resp
        assert "groups_id" in resp["error"]
        assert "never writable" in resp["error"]

    async def test_sanitizes_values_via_rbac(self) -> None:
        rbac_config = RBACConfig(
            sensitive_fields={
                "hr.employee": {
                    "required_group": "hr.group_hr_manager",
                    "fields": ["salary"],
                },
            },
        )
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="hr.employee",
            record_id=1,
            values={"name": "John", "salary": 60000},
        )

        call_args = mock_client.execute_kw.call_args[0][2]
        # call_args is [[record_id], values]
        written_values = call_args[1]
        assert "salary" not in written_values
        assert resp["success"] is True

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("no write permission"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"name": "X"},
        )

        assert "error" in resp
        assert "Access denied" in resp["error"]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"name": "X"},
        )

        assert "error" in resp

    async def test_calls_write_method(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="res.partner",
            record_id=5,
            values={"name": "Updated"},
        )

        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "res.partner"
        assert call_args[1] == "write"
        assert call_args[2] == [[5], {"name": "Updated"}]

    async def test_odoo_validation_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooValidationError("invalid value"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"email": "bad"},
        )

        assert "error" in resp
        assert "Validation error" in resp["error"]

    async def test_admin_write_only_non_admin_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_write_only=["res.company"],
            ),
            is_admin=False,
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.company",
            record_id=1,
            values={"name": "Changed"},
        )

        assert "error" in resp
        assert "administrator" in resp["error"]

    async def test_admin_write_only_admin_succeeds(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_write_only=["res.company"],
            ),
            mock_client=mock_client,
            is_admin=True,
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.company",
            record_id=1,
            values={"name": "Changed"},
        )

        assert resp["success"] is True
