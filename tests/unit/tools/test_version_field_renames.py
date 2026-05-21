"""Tests that the v19 adapter rewrites legacy field names in CRUD calls.

The active :class:`VersionAdapter` is stored on ``GatewayContext`` during
``login``.  Once it is set, CRUD tools translate user-supplied field
names (``tax_id``, ``product_uom``) into the v19-canonical names
(``tax_ids``, ``product_uom_id``) before issuing the Odoo RPC call.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.discovery.models import FieldInfo
from odoo_mcp_gateway.core.security.config_loader import ModelAccessConfig
from odoo_mcp_gateway.core.version.adapters import V17Adapter, V19Adapter
from odoo_mcp_gateway.tools.crud import (
    _apply_field_renames,
    _apply_field_renames_with_suffix,
    _apply_value_renames,
    register_crud_tools,
)

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any, name: str) -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for tool_name, tool in server._tool_manager._tools.items():
        if tool_name == name:
            return tool.fn
    raise AssertionError(f"Tool {name!r} not found")


def _v19_gateway(**kwargs: Any) -> Any:
    """Build a gateway whose version_adapter is V19Adapter."""
    # Make sure sale.order.line is allowed by default policies.
    model_access = kwargs.pop(
        "model_access_config",
        ModelAccessConfig(
            default_policy="allow",
            stock_models={
                "full_crud": [
                    "res.partner",
                    "sale.order",
                    "sale.order.line",
                    "crm.lead",
                ],
            },
        ),
    )
    gateway = make_gateway(model_access_config=model_access, **kwargs)
    gateway.version_adapter = V19Adapter()
    return gateway


# ---------------------------------------------------------------------------
# Unit-level: pure helper behaviour
# ---------------------------------------------------------------------------


class TestApplyFieldRenamesHelper:
    def test_no_adapter_passes_through(self) -> None:
        gateway = make_gateway()
        assert gateway.version_adapter is None
        assert _apply_field_renames(gateway, "sale.order.line", ["tax_id"]) == [
            "tax_id"
        ]

    def test_v17_adapter_has_no_renames(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V17Adapter()
        assert _apply_field_renames(gateway, "sale.order.line", ["tax_id"]) == [
            "tax_id"
        ]

    def test_v19_rewrites_known_legacy_field(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        assert _apply_field_renames(gateway, "sale.order.line", ["tax_id", "name"]) == [
            "tax_ids",
            "name",
        ]

    def test_v19_unknown_model_passthrough(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        assert _apply_field_renames(gateway, "res.partner", ["name", "tax_id"]) == [
            "name",
            "tax_id",
        ]

    def test_empty_input_is_noop(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        assert _apply_field_renames(gateway, "sale.order.line", None) is None
        assert _apply_field_renames(gateway, "sale.order.line", []) == []

    def test_adapter_exception_does_not_crash(self) -> None:
        """A broken adapter must not break CRUD; we just pass through."""

        class BrokenAdapter:
            def get_renamed_fields(self, model: str) -> dict[str, str]:
                raise RuntimeError("kaboom")

        gateway = make_gateway()
        gateway.version_adapter = BrokenAdapter()
        assert _apply_field_renames(gateway, "sale.order.line", ["tax_id"]) == [
            "tax_id"
        ]


class TestApplyFieldRenamesWithSuffix:
    def test_preserves_aggregate_suffix(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        # tax_id has no aggregate use, but use product_uom which is renamed too
        result = _apply_field_renames_with_suffix(
            gateway, "sale.order.line", ["product_uom:count"]
        )
        assert result == ["product_uom_id:count"]

    def test_preserves_temporal_suffix(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        result = _apply_field_renames_with_suffix(
            gateway, "sale.order.line", ["create_date:month"]
        )
        # create_date isn't renamed
        assert result == ["create_date:month"]

    def test_no_adapter_passes_through(self) -> None:
        gateway = make_gateway()
        result = _apply_field_renames_with_suffix(
            gateway, "sale.order.line", ["tax_id:count"]
        )
        assert result == ["tax_id:count"]


class TestApplyValueRenames:
    def test_no_adapter_passthrough(self) -> None:
        gateway = make_gateway()
        assert _apply_value_renames(gateway, "sale.order.line", {"tax_id": [1, 2]}) == {
            "tax_id": [1, 2]
        }

    def test_v19_rewrites_keys(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        out = _apply_value_renames(
            gateway,
            "sale.order.line",
            {"tax_id": [[6, 0, [1, 2]]], "name": "Line 1"},
        )
        assert out == {"tax_ids": [[6, 0, [1, 2]]], "name": "Line 1"}

    def test_v19_user_supplies_both_old_and_new_keeps_new(self) -> None:
        """If user already used the canonical v19 name, don't clobber it."""
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        out = _apply_value_renames(
            gateway,
            "sale.order.line",
            {"tax_ids": [[6, 0, [9]]], "tax_id": [[6, 0, [1, 2]]]},
        )
        assert out["tax_ids"] == [[6, 0, [9]]]

    def test_empty_dict_is_noop(self) -> None:
        gateway = make_gateway()
        gateway.version_adapter = V19Adapter()
        assert _apply_value_renames(gateway, "sale.order.line", {}) == {}


# ---------------------------------------------------------------------------
# Integration-level: real tool calls reach Odoo with rewritten fields
# ---------------------------------------------------------------------------


class TestSearchReadFieldRename:
    async def test_v19_tax_id_translated_to_tax_ids(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "search_read")

        await fn(model="sale.order.line", fields=["tax_id", "name"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["fields"] == ["tax_ids", "name"]

    async def test_v17_does_not_translate(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = _v19_gateway(mock_client=mock_client)
        gateway.version_adapter = V17Adapter()
        fn = _get_tool(gateway, "search_read")

        await fn(model="sale.order.line", fields=["tax_id"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["fields"] == ["tax_id"]

    async def test_no_adapter_does_not_translate(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = _v19_gateway(mock_client=mock_client)
        gateway.version_adapter = None
        fn = _get_tool(gateway, "search_read")

        await fn(model="sale.order.line", fields=["tax_id"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["fields"] == ["tax_id"]

    async def test_smart_fields_path_still_works(self) -> None:
        """When fields=None, smart-field selection runs and renames still apply."""
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = _v19_gateway(mock_client=mock_client)
        # Pre-populate cache so smart-field selection returns ``tax_id``
        gateway.field_inspector._cache[("sale.order.line", None)] = (
            999999999.0,
            {
                "tax_id": FieldInfo(
                    name="tax_id",
                    field_type="many2many",
                    string="Taxes",
                    required=True,
                    store=True,
                ),
            },
        )
        fn = _get_tool(gateway, "search_read")
        await fn(model="sale.order.line")

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        # ``tax_id`` came from the (mocked) field cache but should still be
        # rewritten before reaching Odoo on a v19 server.
        assert "tax_ids" in call_kwargs["fields"]
        assert "tax_id" not in call_kwargs["fields"]


class TestGetRecordFieldRename:
    async def test_v19_translates_fields_on_read(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "tax_ids": [1]}],
        )
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "get_record")

        await fn(model="sale.order.line", record_id=1, fields=["tax_id"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["fields"] == ["tax_ids"]


class TestCreateUpdateFieldRename:
    async def test_v19_rewrites_create_values(self) -> None:
        mock_client = make_mock_client(execute_kw_return=42)
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="sale.order.line",
            values={"name": "x", "tax_id": [[6, 0, [1]]]},
        )

        assert resp["id"] == 42
        sent_values = mock_client.execute_kw.call_args[0][2][0]
        assert "tax_ids" in sent_values
        assert "tax_id" not in sent_values

    async def test_v19_rewrites_update_values(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "update_record")

        await fn(
            model="sale.order.line",
            record_id=1,
            values={"product_uom": 5},
        )

        sent_values = mock_client.execute_kw.call_args[0][2][1]
        assert "product_uom_id" in sent_values
        assert "product_uom" not in sent_values

    async def test_dry_run_shows_rewritten_payload(self) -> None:
        """dry_run should reflect what would actually be sent to Odoo."""
        mock_client = make_mock_client(execute_kw_return=None)
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="sale.order.line",
            values={"tax_id": [[6, 0, [1]]]},
            dry_run=True,
        )

        assert resp["dry_run"] is True
        assert "tax_ids" in resp["validated_values"]
        assert "tax_id" not in resp["validated_values"]


class TestReadGroupFieldRename:
    async def test_v19_rewrites_groupby_field(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "read_group")

        await fn(
            model="sale.order.line",
            fields=["product_uom"],
            groupby=["product_uom"],
        )

        sent_fields = mock_client.execute_kw.call_args[0][2][1]
        sent_groupby = mock_client.execute_kw.call_args[0][2][2]
        assert sent_fields == ["product_uom_id"]
        assert sent_groupby == ["product_uom_id"]

    async def test_v19_preserves_suffix_on_groupby(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = _v19_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "read_group")

        await fn(
            model="sale.order.line",
            fields=["product_uom:count"],
            groupby=["create_date:month"],
        )

        sent_fields = mock_client.execute_kw.call_args[0][2][1]
        sent_groupby = mock_client.execute_kw.call_args[0][2][2]
        assert sent_fields == ["product_uom_id:count"]
        # create_date isn't renamed, but suffix is preserved
        assert sent_groupby == ["create_date:month"]
