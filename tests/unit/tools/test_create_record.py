"""Tests for the create_record tool."""

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
        if name == "create_record":
            return tool.fn
    raise AssertionError("create_record tool not found")


class TestCreateRecord:
    async def test_returns_new_id(self) -> None:
        mock_client = make_mock_client(execute_kw_return=42)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", values={"name": "Test"})

        assert resp["id"] == 42
        assert resp["model"] == "res.partner"

    async def test_read_only_model_fails(self) -> None:
        gateway = make_gateway(
            model_access_config=ModelAccessConfig(
                default_policy="deny",
                stock_models={"read_only": ["res.currency"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.currency", values={"name": "USD"})

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
            values={"key": "x", "value": "y"},
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_strips_blocked_write_fields(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                blocked_write_fields=["password"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="res.partner",
            values={"name": "Test", "password": "secret"},
        )

        assert "error" in resp
        assert "password" in resp["error"]
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
        mock_client = make_mock_client(execute_kw_return=10)
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="hr.employee",
            values={"name": "John", "salary": 50000},
        )

        # The salary field should be stripped by RBAC sanitization
        call_args = mock_client.execute_kw.call_args[0][2]
        assert "salary" not in call_args[0]
        assert resp["id"] == 10

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("no create permission"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", values={"name": "Test"})

        assert "error" in resp
        assert "Access denied" in resp["error"]

    async def test_odoo_validation_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooValidationError("required field missing"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", values={"email": "test@test.com"})

        assert "error" in resp
        assert "Validation error" in resp["error"]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", values={"name": "Test"})

        assert "error" in resp

    async def test_admin_write_only_non_admin_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_write_only=["res.company"],
            ),
            is_admin=False,
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.company", values={"name": "My Co"})

        assert "error" in resp
        assert "administrator" in resp["error"]

    async def test_admin_write_only_admin_succeeds(self) -> None:
        mock_client = make_mock_client(execute_kw_return=1)
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_write_only=["res.company"],
            ),
            mock_client=mock_client,
            is_admin=True,
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.company", values={"name": "My Co"})

        assert "id" in resp
        assert resp["id"] == 1

    async def test_calls_execute_kw_with_create(self) -> None:
        mock_client = make_mock_client(execute_kw_return=5)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", values={"name": "X"})

        mock_client.execute_kw.assert_called_once()
        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "res.partner"
        assert call_args[1] == "create"
