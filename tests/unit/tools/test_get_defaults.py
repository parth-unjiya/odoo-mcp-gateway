"""Tests for the get_defaults tool."""

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
        if name == "get_defaults":
            return tool.fn
    raise AssertionError("get_defaults tool not found")


class TestGetDefaults:
    async def test_returns_defaults(self) -> None:
        defaults = {"name": "", "active": True}
        mock_client = make_mock_client(execute_kw_return=defaults)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", fields=["name", "active"])

        assert "defaults" in resp
        assert resp["defaults"] == {"name": "", "active": True}
        assert resp["model"] == "res.partner"

    async def test_not_authenticated(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", fields=["name"])

        assert "error" in resp

    async def test_blocked_model(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="ir.config_parameter", fields=["key"])

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_with_specific_fields(self) -> None:
        defaults = {"name": "Default", "active": True}
        mock_client = make_mock_client(execute_kw_return=defaults)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name", "active"])

        # Verify the fields were passed to execute_kw
        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "res.partner"
        assert call_args[1] == "default_get"
        assert call_args[2] == [["name", "active"]]

    async def test_without_fields_uses_field_inspector(self) -> None:
        """When no fields are provided, writable fields are fetched from inspector."""
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        defaults = {"name": ""}
        mock_client = make_mock_client(execute_kw_return=defaults)
        gateway = make_gateway(mock_client=mock_client)

        # Pre-populate field cache
        gateway.field_inspector._cache[("res.partner", None)] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                    readonly=False,
                    store=True,
                ),
                "display_name": FieldInfo(
                    name="display_name",
                    field_type="char",
                    string="Display Name",
                    readonly=True,
                    store=False,
                ),
                "email": FieldInfo(
                    name="email",
                    field_type="char",
                    string="Email",
                    readonly=False,
                    store=True,
                ),
            },
        )

        fn = _get_tool(gateway)
        await fn(model="res.partner")

        # Verify only writable fields were passed
        call_args = mock_client.execute_kw.call_args[0]
        fields_passed = call_args[2][0]
        assert "name" in fields_passed
        assert "email" in fields_passed
        assert "display_name" not in fields_passed
