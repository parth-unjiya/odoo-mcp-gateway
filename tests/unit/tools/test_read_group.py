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

    async def test_default_limit_caps_at_500(self) -> None:
        """When limit is None, default cap of 500 is applied."""
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway)
        await fn(
            model="account.move.line",
            fields=["balance:sum"],
            groupby=["partner_id"],
        )
        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs.get("limit") == 500

    async def test_explicit_limit_clamped(self) -> None:
        """Explicit limit > 500 is clamped to 500."""
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
            limit=10000,
        )
        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["limit"] == 500

    async def test_falls_back_to_formatted_read_group(self) -> None:
        """read_group 'method not found' falls back to formatted_read_group."""
        fallback_groups = [
            {"state": "draft", "__count": 5},
        ]
        mock_client = make_mock_client()

        call_log: list[str] = []

        async def execute_kw(
            model: str,
            method: str,
            args: list[Any],
            kwargs: dict[str, Any] | None = None,
        ) -> Any:
            call_log.append(method)
            if method == "read_group":
                # Simulate Odoo "method not found" type error
                raise Exception("read_group does not exist on model sale.order")
            if method == "formatted_read_group":
                return fallback_groups
            raise AssertionError(f"unexpected method {method}")

        mock_client.execute_kw = AsyncMock(side_effect=execute_kw)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        assert "error" not in resp
        assert resp["groups"] == fallback_groups
        assert call_log == ["read_group", "formatted_read_group"]

    async def test_fallback_reraises_original_when_both_fail(self) -> None:
        """If both read_group and formatted_read_group fail, the original error wins."""
        mock_client = make_mock_client()

        async def execute_kw(
            model: str,
            method: str,
            args: list[Any],
            kwargs: dict[str, Any] | None = None,
        ) -> Any:
            if method == "read_group":
                raise OdooAccessError("read_group method not found")
            if method == "formatted_read_group":
                raise OdooAccessError("formatted_read_group also broken")
            raise AssertionError(f"unexpected method {method}")

        mock_client.execute_kw = AsyncMock(side_effect=execute_kw)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        assert "error" in resp
        # The sanitized error message should mention access denial
        # (OdooAccessError -> "Access denied")
        assert "Access denied" in resp["error"]

    async def test_non_fallback_errors_propagate(self) -> None:
        """Errors that aren't 'method not found' should propagate unchanged."""
        mock_client = make_mock_client()

        async def execute_kw(
            model: str,
            method: str,
            args: list[Any],
            kwargs: dict[str, Any] | None = None,
        ) -> Any:
            if method == "read_group":
                # A real ACL/domain error, NOT a missing-method error
                raise OdooAccessError("you do not have access")
            raise AssertionError(f"unexpected fallback to {method}")

        mock_client.execute_kw = AsyncMock(side_effect=execute_kw)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["state"],
            groupby=["state"],
        )

        assert "error" in resp
        # Only ONE call should have happened (no fallback)
        assert mock_client.execute_kw.call_count == 1

    async def test_temporal_groupby(self) -> None:
        """Temporal groupby syntax (e.g. create_date:month) passes validation."""
        groups = [
            {"create_date:month": "March 2025", "amount_total": 1500},
        ]
        mock_client = make_mock_client(execute_kw_return=groups)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            fields=["amount_total:sum"],
            groupby=["create_date:month"],
        )

        assert "error" not in resp
        assert "groups" in resp
        # Verify the groupby was forwarded to the mock client correctly
        call_args = mock_client.execute_kw.call_args[0]
        assert call_args[0] == "sale.order"
        assert call_args[1] == "read_group"
        assert call_args[2][2] == ["create_date:month"]
