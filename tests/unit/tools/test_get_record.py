"""Tests for the get_record tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAccessError
from odoo_mcp_gateway.core.security.config_loader import (
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any) -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == "get_record":
            return tool.fn
    raise AssertionError("get_record tool not found")


class TestGetRecord:
    async def test_returns_record(self) -> None:
        record = [{"id": 1, "name": "Test Partner", "email": "test@test.com"}]
        mock_client = make_mock_client(execute_kw_return=record)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=1)

        assert "record" in resp
        assert resp["record"]["id"] == 1
        assert resp["record"]["name"] == "Test Partner"
        assert resp["model"] == "res.partner"

    async def test_not_found(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=999)

        assert "error" in resp
        assert "not found" in resp["error"]

    async def test_with_explicit_fields(self) -> None:
        record = [{"id": 1, "name": "Test"}]
        mock_client = make_mock_client(execute_kw_return=record)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="res.partner",
            record_id=1,
            fields=["name", "email"],
        )

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["fields"] == ["name", "email"]

    async def test_blocked_model_returns_error(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="ir.config_parameter", record_id=1)

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_applies_rbac_filtering(self) -> None:
        record = [{"id": 1, "name": "Test", "salary": 50000}]
        rbac_config = RBACConfig(
            sensitive_fields={
                "hr.employee": {
                    "required_group": "hr.group_hr_manager",
                    "fields": ["salary"],
                },
            },
        )
        mock_client = make_mock_client(execute_kw_return=record)
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )

        fn = _get_tool(gateway)
        resp = await fn(model="hr.employee", record_id=1)

        assert resp["record"]["salary"] == "***"
        assert resp["record"]["name"] == "Test"

    async def test_calls_read_method(self) -> None:
        record = [{"id": 5, "name": "X"}]
        mock_client = make_mock_client(execute_kw_return=record)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", record_id=5)

        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "res.partner"
        assert call_args[1] == "read"
        assert call_args[2] == [[5]]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=1)

        assert "error" in resp

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("cannot read"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", record_id=1)

        assert "error" in resp
        assert "Access denied" in resp["error"]

    async def test_no_fields_sends_empty_kwargs(self) -> None:
        record = [{"id": 1, "name": "X"}]
        mock_client = make_mock_client(execute_kw_return=record)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", record_id=1)

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert "fields" not in call_kwargs or call_kwargs.get("fields") is None
