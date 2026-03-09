"""Tests for the read_group tool."""

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
        if name == "read_group":
            return tool.fn
    raise AssertionError("read_group tool not found")


class TestReadGroup:
    async def test_returns_grouped_data(self) -> None:
        groups = [
            {"state": "draft", "state_count": 5, "__domain": []},
            {"state": "done", "state_count": 3, "__domain": []},
        ]
        mock_client = make_mock_client(execute_kw_return=groups)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        assert "groups" in resp
        assert len(resp["groups"]) == 2
        assert resp["model"] == "sale.order"

    async def test_blocked_model_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="ir.config_parameter",
            fields=["key"],
            groupby=["key"],
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_with_domain(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        domain = [["active", "=", True]]
        await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
            domain=domain,
        )

        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args[0] == domain

    async def test_with_limit(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
            limit=10,
        )

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["limit"] == 10

    async def test_with_orderby(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
            orderby="state asc",
        )

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["orderby"] == "state asc"

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        assert "error" in resp

    async def test_calls_read_group_method(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "sale.order"
        assert call_args[1] == "read_group"

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("no read_group"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        assert "error" in resp
        assert "Access denied" in resp["error"]
