"""Tests for the search_count tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAccessError
from odoo_mcp_gateway.core.security.config_loader import (
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any) -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == "search_count":
            return tool.fn
    raise AssertionError("search_count tool not found")


class TestSearchCount:
    async def test_returns_count(self) -> None:
        mock_client = make_mock_client(execute_kw_return=42)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner")

        assert resp["count"] == 42
        assert resp["model"] == "res.partner"

    async def test_with_domain(self) -> None:
        mock_client = make_mock_client(execute_kw_return=10)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        domain = [["active", "=", True]]
        resp = await fn(model="res.partner", domain=domain)

        assert resp["count"] == 10
        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args == [domain]

    async def test_blocked_model_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="ir.config_parameter")

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_default_domain_is_empty(self) -> None:
        mock_client = make_mock_client(execute_kw_return=0)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner")

        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args == [[]]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner")

        assert "error" in resp

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("no count permission"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner")

        assert "error" in resp
        assert "Access denied" in resp["error"]
