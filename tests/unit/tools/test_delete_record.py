"""Tests for the delete_record tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAccessError
from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any) -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == "delete_record":
            return tool.fn
    raise AssertionError("delete_record tool not found")


class TestDeleteRecord:
    async def test_success(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=1)

        assert resp["success"] is True
        assert resp["model"] == "res.partner"
        assert resp["id"] == 1

    async def test_blocked_model_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="ir.config_parameter", record_id=1)

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_read_only_model_fails(self) -> None:
        gateway = make_gateway(
            model_access_config=ModelAccessConfig(
                default_policy="deny",
                stock_models={"read_only": ["res.currency"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.currency", record_id=1)

        assert "error" in resp
        assert "read-only" in resp["error"]

    async def test_calls_unlink(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", record_id=7)

        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "res.partner"
        assert call_args[1] == "unlink"
        assert call_args[2] == [[7]]

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("cannot delete"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=1)

        assert "error" in resp
        assert "Access denied" in resp["error"]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=1)

        assert "error" in resp

    async def test_admin_only_model_non_admin_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_only=["res.users"],
            ),
            is_admin=False,
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.users", record_id=1)

        assert "error" in resp
        assert "administrator" in resp["error"]

    async def test_admin_only_model_admin_succeeds(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_only=["res.users"],
            ),
            mock_client=mock_client,
            is_admin=True,
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.users", record_id=1)

        assert resp["success"] is True
