"""Tests for dry_run mode on state-change tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any, tool_name: str) -> Any:
    """Extract a tool function by name from the registered CRUD tools."""
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == tool_name:
            return tool.fn
    raise AssertionError(f"{tool_name} tool not found")


class TestCreateRecordDryRun:
    async def test_dry_run_validates_without_creating(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": "Test"}, dry_run=True)

        assert resp["dry_run"] is True
        assert resp["action"] == "create"
        assert resp["model"] == "res.partner"
        assert resp["validated_values"] == {"name": "Test"}
        assert resp["field_count"] == 1
        # Verify Odoo was NOT called
        mock_client.execute_kw.assert_not_called()

    async def test_dry_run_still_checks_restrictions(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(always_blocked=["ir.config_parameter"]),
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="ir.config_parameter", values={"key": "x"}, dry_run=True
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_dry_run_still_checks_blocked_write_fields(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(blocked_write_fields=["password"]),
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="res.partner",
            values={"name": "Test", "password": "secret"},
            dry_run=True,
        )

        assert "error" in resp
        assert "password" in resp["error"]

    async def test_dry_run_shows_rbac_sanitized_values(self) -> None:
        from odoo_mcp_gateway.core.security.config_loader import RBACConfig

        rbac_config = RBACConfig(
            sensitive_fields={
                "hr.employee": {
                    "required_group": "hr.group_hr_manager",
                    "fields": ["salary"],
                },
            },
        )
        mock_client = make_mock_client()
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="hr.employee",
            values={"name": "John", "salary": 50000},
            dry_run=True,
        )

        assert resp["dry_run"] is True
        # salary should have been stripped by RBAC
        assert "salary" not in resp["validated_values"]
        assert resp["validated_values"] == {"name": "John"}
        mock_client.execute_kw.assert_not_called()

    async def test_normal_mode_still_works(self) -> None:
        mock_client = make_mock_client(execute_kw_return=42)
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": "Test"})

        assert resp["id"] == 42
        mock_client.execute_kw.assert_called_once()


class TestUpdateRecordDryRun:
    async def test_dry_run_validates_without_updating(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="res.partner", record_id=1, values={"name": "New"}, dry_run=True
        )

        assert resp["dry_run"] is True
        assert resp["action"] == "update"
        assert resp["id"] == 1
        assert resp["model"] == "res.partner"
        assert resp["validated_values"] == {"name": "New"}
        assert resp["field_count"] == 1
        mock_client.execute_kw.assert_not_called()

    async def test_dry_run_still_checks_restrictions(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(always_blocked=["ir.config_parameter"]),
        )
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="ir.config_parameter",
            record_id=1,
            values={"value": "x"},
            dry_run=True,
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_dry_run_still_checks_blocked_write_fields(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(blocked_write_fields=["groups_id"]),
        )
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"name": "Test", "groups_id": [[6, 0, [1]]]},
            dry_run=True,
        )

        assert "error" in resp
        assert "groups_id" in resp["error"]

    async def test_normal_mode_still_works(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="res.partner", record_id=1, values={"name": "Updated"}
        )

        assert resp["success"] is True
        mock_client.execute_kw.assert_called_once()


class TestDeleteRecordDryRun:
    async def test_dry_run_validates_without_deleting(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "delete_record")

        resp = await fn(model="res.partner", record_id=1, dry_run=True)

        assert resp["dry_run"] is True
        assert resp["action"] == "delete"
        assert resp["id"] == 1
        assert resp["model"] == "res.partner"
        mock_client.execute_kw.assert_not_called()

    async def test_dry_run_still_checks_restrictions(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(always_blocked=["ir.config_parameter"]),
        )
        fn = _get_tool(gateway, "delete_record")

        resp = await fn(model="ir.config_parameter", record_id=1, dry_run=True)

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_dry_run_rejects_invalid_record_id(self) -> None:
        gateway = make_gateway()
        fn = _get_tool(gateway, "delete_record")

        resp = await fn(model="res.partner", record_id=-1, dry_run=True)

        assert "error" in resp
        assert "positive integer" in resp["error"]

    async def test_normal_mode_still_works(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "delete_record")

        resp = await fn(model="res.partner", record_id=1)

        assert resp["success"] is True
        mock_client.execute_kw.assert_called_once()


class TestExecuteMethodDryRun:
    async def test_dry_run_validates_without_executing(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=ModelAccessConfig(
                default_policy="allow",
                stock_models={"full_crud": ["sale.order"]},
                allowed_methods={"sale.order": ["action_confirm"]},
            ),
        )
        fn = _get_tool(gateway, "execute_method")

        resp = await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
            dry_run=True,
        )

        assert resp["dry_run"] is True
        assert resp["model"] == "sale.order"
        assert resp["method"] == "action_confirm"
        assert resp["record_ids"] == [1]
        assert resp["args_count"] == 1  # record_ids is one arg in call_args
        mock_client.execute_kw.assert_not_called()

    async def test_dry_run_still_checks_restrictions(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(always_blocked=["ir.config_parameter"]),
        )
        fn = _get_tool(gateway, "execute_method")

        resp = await fn(
            model="ir.config_parameter",
            method="set_param",
            dry_run=True,
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_dry_run_still_blocks_orm_methods(self) -> None:
        gateway = make_gateway(is_admin=True)
        fn = _get_tool(gateway, "execute_method")

        resp = await fn(
            model="res.partner",
            method="read",
            record_ids=[1],
            dry_run=True,
        )

        assert "error" in resp
        assert "cannot be called" in resp["error"].lower()

    async def test_dry_run_without_record_ids(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=ModelAccessConfig(
                default_policy="allow",
                stock_models={"full_crud": ["sale.order"]},
                allowed_methods={"sale.order": ["get_report_data"]},
            ),
        )
        fn = _get_tool(gateway, "execute_method")

        resp = await fn(
            model="sale.order",
            method="get_report_data",
            args=["some_arg"],
            dry_run=True,
        )

        assert resp["dry_run"] is True
        assert resp["record_ids"] is None
        assert resp["args_count"] == 1  # just "some_arg"
        mock_client.execute_kw.assert_not_called()

    async def test_normal_mode_still_works(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=ModelAccessConfig(
                default_policy="allow",
                stock_models={"full_crud": ["sale.order"]},
                allowed_methods={"sale.order": ["action_confirm"]},
            ),
        )
        fn = _get_tool(gateway, "execute_method")

        resp = await fn(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
        )

        assert "result" in resp
        mock_client.execute_kw.assert_called_once()
