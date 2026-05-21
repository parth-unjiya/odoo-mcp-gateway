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
        # Verify the actual create RPC was NOT issued. The pre-flight
        # ``fields_get`` probe may run (and that's expected — dry_run
        # mirrors the real validation pipeline) but no ``create`` call.
        create_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "create"
        ]
        assert create_calls == []

    async def test_dry_run_still_checks_restrictions(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"]
            ),
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="ir.config_parameter", values={"key": "x"}, dry_run=True)

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
        create_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "create"
        ]
        assert create_calls == []

    async def test_normal_mode_still_works(self) -> None:
        mock_client = make_mock_client(execute_kw_return=42)
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": "Test"})

        assert resp["id"] == 42
        # Pre-flight readonly check now adds a ``fields_get`` call before
        # the ``create``. We assert that ``create`` actually fired rather
        # than counting the total number of calls.
        create_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "create"
        ]
        assert len(create_calls) == 1


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
        # Pre-flight ``fields_get`` may run; the actual ``write`` must not.
        write_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "write"
        ]
        assert write_calls == []

    async def test_dry_run_still_checks_restrictions(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"]
            ),
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

        resp = await fn(model="res.partner", record_id=1, values={"name": "Updated"})

        assert resp["success"] is True
        # Pre-flight readonly check now adds a ``fields_get`` call before
        # the ``write``. Assert that ``write`` itself fired rather than
        # counting total calls.
        write_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "write"
        ]
        assert len(write_calls) == 1


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
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"]
            ),
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
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"]
            ),
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


class TestDryRunRunsValidation:
    """Regression tests for the v0.2.2 fix where dry_run skipped
    ``_validate_writable_fields``. Pass-2 finding P2-4 showed that
    callers could pre-flight via dry_run and get false-positive
    validation — empty-required strings and readonly/computed writes
    silently passed dry_run but failed the real call.

    After the fix, dry_run runs the SAME validation pipeline as the
    real call. Only the actual Odoo RPC is skipped.
    """

    async def test_create_dry_run_rejects_readonly_field(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        # Seed the field cache with a readonly/computed field
        gateway.field_inspector._cache[("res.partner", None)] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                    store=True,
                ),
                "amount_total": FieldInfo(
                    name="amount_total",
                    field_type="float",
                    string="Total",
                    readonly=True,
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="res.partner",
            values={"name": "X", "amount_total": 999},
            dry_run=True,
        )

        # dry_run MUST report the readonly violation, not silently
        # validate a payload that would fail at the real call.
        assert "error" in resp
        assert "readonly" in resp["error"] or "computed" in resp["error"]
        mock_client.execute_kw.assert_not_called()

    async def test_create_dry_run_rejects_empty_required(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        # Use res.partner — it's in the default test full_crud — and
        # seed the field cache marking ``name`` as required.
        gateway.field_inspector._cache[("res.partner", None)] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="res.partner",
            values={"name": "   "},  # whitespace-only
            dry_run=True,
        )

        assert "error" in resp
        assert "empty" in resp["error"].lower() or "required" in resp["error"].lower()
        create_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "create"
        ]
        assert create_calls == []

    async def test_update_dry_run_rejects_readonly_field(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache[("sale.order", None)] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    store=True,
                ),
                "amount_total": FieldInfo(
                    name="amount_total",
                    field_type="float",
                    string="Total",
                    readonly=True,
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="sale.order",
            record_id=1,
            values={"amount_total": 999999},
            dry_run=True,
        )

        assert "error" in resp
        assert "readonly" in resp["error"] or "computed" in resp["error"]
        mock_client.execute_kw.assert_not_called()


class TestDryRunDateAndSelectionValidation:
    """v0.2.2 gap fix: dry_run now validates field types (date, selection,
    integer, float, boolean) against the model's field metadata. Previously
    these were accepted in dry_run and only failed at the real call.
    """

    async def test_dry_run_rejects_invalid_date_format(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache[("sale.order", None)] = (
            999999999.0,
            {
                "date_order": FieldInfo(
                    name="date_order",
                    field_type="datetime",
                    string="Order Date",
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="sale.order",
            values={"date_order": "not-a-date"},
            dry_run=True,
        )
        assert "error" in resp
        assert (
            "not a valid" in resp["error"].lower()
            or "datetime" in resp["error"].lower()
        )

    async def test_dry_run_rejects_invalid_calendar_date(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache[("res.partner", None)] = (
            999999999.0,
            {
                "birthdate": FieldInfo(
                    name="birthdate",
                    field_type="date",
                    string="Birth Date",
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        # "2026-13-99" matches the regex shape but is not a valid date
        resp = await fn(
            model="res.partner",
            values={"birthdate": "2026-13-99"},
            dry_run=True,
        )
        assert "error" in resp
        assert "valid" in resp["error"].lower()

    async def test_dry_run_rejects_invalid_selection_value(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache[("res.partner", None)] = (
            999999999.0,
            {
                "lang": FieldInfo(
                    name="lang",
                    field_type="selection",
                    string="Language",
                    store=True,
                    selection=[("en_US", "English"), ("fr_FR", "French")],
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="res.partner",
            values={"lang": "klingon"},
            dry_run=True,
        )
        assert "error" in resp
        assert "klingon" in resp["error"]

    async def test_dry_run_accepts_valid_date(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache[("sale.order", None)] = (
            999999999.0,
            {
                "date_order": FieldInfo(
                    name="date_order",
                    field_type="datetime",
                    string="Order Date",
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="sale.order",
            values={"date_order": "2026-06-15 10:30:00"},
            dry_run=True,
        )
        # Valid datetime — should NOT be in the error path
        assert "error" not in resp
        assert resp["dry_run"] is True

    async def test_dry_run_rejects_non_integer_string_for_int_field(self) -> None:
        from odoo_mcp_gateway.core.discovery.models import FieldInfo

        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache[("res.partner", None)] = (
            999999999.0,
            {
                "color": FieldInfo(
                    name="color",
                    field_type="integer",
                    string="Color",
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="res.partner",
            values={"color": "blue"},
            dry_run=True,
        )
        assert "error" in resp
        assert "integer" in resp["error"].lower()
