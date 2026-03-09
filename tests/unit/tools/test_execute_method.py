"""Tests for the execute_method tool."""

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
        if name == "execute_method":
            return tool.fn
    raise AssertionError("execute_method tool not found")


def _model_access_with_methods(
    methods: dict[str, list[str]],
    full_crud: list[str] | None = None,
) -> ModelAccessConfig:
    """Helper to create ModelAccessConfig with allowed methods and full_crud."""
    return ModelAccessConfig(
        default_policy="allow",
        stock_models={
            "full_crud": full_crud or ["sale.order"],
        },
        allowed_methods=methods,
    )


class TestExecuteMethodAllowed:
    async def test_allowed_method_success(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["action_confirm"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
        )

        assert "result" in resp
        assert resp["result"] is True
        assert resp["model"] == "sale.order"
        assert resp["method"] == "action_confirm"

    async def test_with_record_ids_prepended_to_args(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["action_confirm"]},
            ),
        )

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1, 2, 3],
        )

        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args[0] == [1, 2, 3]

    async def test_with_extra_args(self) -> None:
        mock_client = make_mock_client(execute_kw_return="ok")
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["custom_method"]},
            ),
        )

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            method="custom_method",
            record_ids=[5],
            args=["extra_arg"],
        )

        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args == [[5], "extra_arg"]

    async def test_with_kwargs(self) -> None:
        mock_client = make_mock_client(execute_kw_return="done")
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["custom_method"]},
            ),
        )

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            method="custom_method",
            kwargs={"force": True},
        )

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs == {"force": True}

    async def test_without_record_ids(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["get_report_data"]},
            ),
        )

        fn = _get_tool(gateway)
        await fn(
            model="sale.order",
            method="get_report_data",
            args=["some_arg"],
        )

        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args == ["some_arg"]


class TestExecuteMethodBlocked:
    async def test_blocked_method_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                blocked_methods=["sudo"],
            ),
            model_access_config=_model_access_with_methods(
                {"sale.order": ["sudo"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="sudo",
            record_ids=[1],
        )

        assert "error" in resp
        assert "not allowed" in resp["error"]

    async def test_private_method_as_non_admin_fails(self) -> None:
        gateway = make_gateway(
            is_admin=False,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["_compute_total"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="_compute_total",
            record_ids=[1],
        )

        assert "error" in resp
        assert "Private methods" in resp["error"] or "administrator" in resp["error"]

    async def test_private_method_as_admin_succeeds(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            mock_client=mock_client,
            is_admin=True,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["_compute_total"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="_compute_total",
            record_ids=[1],
        )

        assert "result" in resp

    async def test_no_allowed_methods_non_admin_fails(self) -> None:
        gateway = make_gateway(
            is_admin=False,
            model_access_config=_model_access_with_methods(
                methods={},
                full_crud=["sale.order"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="custom_action",
            record_ids=[1],
        )

        assert "error" in resp
        assert "not configured" in resp["error"] or "No methods" in resp["error"]

    async def test_blocked_model_fails(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="ir.config_parameter",
            method="set_param",
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_method_not_in_allowed_list(self) -> None:
        gateway = make_gateway(
            is_admin=False,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["action_confirm"]},
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="action_cancel",
            record_ids=[1],
        )

        assert "error" in resp
        assert "not in the allowed list" in resp["error"]


class TestExecuteMethodErrors:
    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
        )

        assert "error" in resp

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("no execute"),
        )
        gateway = make_gateway(
            mock_client=mock_client,
            is_admin=True,
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
        )

        assert "error" in resp
        assert "Access denied" in resp["error"]

    async def test_unexpected_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=RuntimeError("boom"),
        )
        gateway = make_gateway(
            mock_client=mock_client,
            is_admin=True,
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
        )

        assert "error" in resp
        assert resp["error"]  # sanitized error message returned


def _assert_blocked(result: dict) -> None:
    """Assert execute_method returned a blocked-ORM-method error."""
    assert "error" in result
    err = result["error"].lower()
    assert "cannot be called" in err or "crud" in err


class TestOrmMethodBlocking:
    """Verify every ORM method in _BLOCKED_ORM_METHODS is blocked.

    These methods have dedicated CRUD tools with field-level checks.
    Allowing them via execute_method would bypass those protections.
    """

    BLOCKED_ORM_METHODS = [
        "read",
        "search",
        "search_read",
        "search_count",
        "write",
        "create",
        "unlink",
        "copy",
        "export_data",
        "import_data",
        "load",
        "name_create",
    ]

    async def _call(self, method: str) -> dict:
        gateway = make_gateway(is_admin=True)
        fn = _get_tool(gateway)
        return await fn(model="res.partner", method=method)

    async def test_blocks_read(self) -> None:
        _assert_blocked(await self._call("read"))

    async def test_blocks_search(self) -> None:
        _assert_blocked(await self._call("search"))

    async def test_blocks_search_read(self) -> None:
        _assert_blocked(await self._call("search_read"))

    async def test_blocks_search_count(self) -> None:
        _assert_blocked(await self._call("search_count"))

    async def test_blocks_write(self) -> None:
        _assert_blocked(await self._call("write"))

    async def test_blocks_create(self) -> None:
        _assert_blocked(await self._call("create"))

    async def test_blocks_unlink(self) -> None:
        _assert_blocked(await self._call("unlink"))

    async def test_blocks_copy(self) -> None:
        _assert_blocked(await self._call("copy"))

    async def test_blocks_export_data(self) -> None:
        _assert_blocked(await self._call("export_data"))

    async def test_blocks_import_data(self) -> None:
        _assert_blocked(await self._call("import_data"))

    async def test_blocks_load(self) -> None:
        _assert_blocked(await self._call("load"))

    async def test_blocks_name_create(self) -> None:
        _assert_blocked(await self._call("name_create"))

    async def test_orm_blocking_takes_priority_over_admin(self) -> None:
        """Even admin users cannot call blocked ORM methods via execute_method."""
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            mock_client=mock_client,
            is_admin=True,
            model_access_config=_model_access_with_methods(
                {"res.partner": ["read"]},
                full_crud=["res.partner"],
            ),
        )
        fn = _get_tool(gateway)
        result = await fn(model="res.partner", method="read", record_ids=[1])
        assert "error" in result
        # The client should never be called for blocked ORM methods
        mock_client.execute_kw.assert_not_called()

    async def test_orm_blocking_does_not_affect_allowed_methods(self) -> None:
        """Non-ORM methods should still work as expected."""
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            mock_client=mock_client,
            is_admin=True,
            model_access_config=_model_access_with_methods(
                {"sale.order": ["action_confirm"]},
            ),
        )
        fn = _get_tool(gateway)
        result = await fn(model="sale.order", method="action_confirm", record_ids=[1])
        assert "result" in result
        mock_client.execute_kw.assert_called_once()

    async def test_blocked_orm_methods_matches_frozenset(self) -> None:
        """Verify our test list is a subset of the source _BLOCKED_ORM_METHODS."""
        from odoo_mcp_gateway.tools.crud import _BLOCKED_ORM_METHODS

        # Our list must be a subset of the source constant.
        # The source may contain additional methods (e.g. read_group,
        # name_search, fields_get) that are blocked for extra safety.
        assert set(self.BLOCKED_ORM_METHODS).issubset(_BLOCKED_ORM_METHODS), (
            f"Test list has methods not in source: "
            f"{set(self.BLOCKED_ORM_METHODS) - _BLOCKED_ORM_METHODS}"
        )

    async def test_all_source_blocked_methods_are_blocked(self) -> None:
        """Verify every method in _BLOCKED_ORM_METHODS is actually blocked."""
        from odoo_mcp_gateway.tools.crud import _BLOCKED_ORM_METHODS

        for method in _BLOCKED_ORM_METHODS:
            result = await self._call(method)
            _assert_blocked(result)
