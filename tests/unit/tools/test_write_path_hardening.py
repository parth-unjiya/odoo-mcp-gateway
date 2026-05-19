"""Tests covering write-path hardening introduced for the Odoo 19 pass.

Each test maps to a finding from the live-testing audit:

* ``id`` field rejected on create / update (P2-5)
* readonly / computed fields rejected pre-flight (P2-6)
* empty / whitespace-only required-field values rejected on create (P2-7)
* ``limit <= 0`` rejected with an explicit error on search_read /
  read_group (P2-11)
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.discovery.models import FieldInfo
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any, tool_name: str) -> Any:
    """Return the registered tool function by name."""
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for name, tool in server._tool_manager._tools.items():
        if name == tool_name:
            return tool.fn
    raise AssertionError(f"{tool_name} not registered")


def _prime_fields(gateway: Any, model: str, fields: dict[str, FieldInfo]) -> None:
    """Seed the field-inspector cache so we don't go through execute_kw."""
    gateway.field_inspector._cache[model] = (999_999_999.0, fields)


# ── P2-5: id field is immutable ────────────────────────────────────


class TestRejectIdField:
    async def test_create_rejects_explicit_id(self) -> None:
        mock_client = make_mock_client(execute_kw_return=1)
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="res.partner",
            values={"id": 999, "name": "Test"},
        )

        assert "error" in resp
        assert "id" in resp["error"]
        assert "immutable" in resp["error"]
        mock_client.execute_kw.assert_not_called()

    async def test_update_rejects_explicit_id(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"id": 2, "name": "Changed"},
        )

        assert "error" in resp
        assert "immutable" in resp["error"]
        mock_client.execute_kw.assert_not_called()


# ── P2-6: readonly / computed fields ───────────────────────────────


class TestReadonlyFieldsRejected:
    async def test_create_rejects_readonly_field(self) -> None:
        mock_client = make_mock_client(execute_kw_return=1)
        gateway = make_gateway(mock_client=mock_client)
        _prime_fields(
            gateway,
            "sale.order",
            {
                "name": FieldInfo(name="name", field_type="char", string="Name"),
                "amount_total": FieldInfo(
                    name="amount_total",
                    field_type="monetary",
                    string="Total",
                    readonly=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(
            model="sale.order",
            values={"name": "SO/100", "amount_total": 500},
        )

        assert "error" in resp
        assert "amount_total" in resp["error"]
        err = resp["error"].lower()
        assert "readonly" in err or "computed" in err
        # No create attempted because pre-flight tripped.
        create_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "create"
        ]
        assert create_calls == []

    async def test_update_rejects_readonly_field(self) -> None:
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)
        _prime_fields(
            gateway,
            "sale.order",
            {
                "amount_total": FieldInfo(
                    name="amount_total",
                    field_type="monetary",
                    string="Total",
                    readonly=True,
                ),
            },
        )
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="sale.order",
            record_id=1,
            values={"amount_total": 0},
        )

        assert "error" in resp
        assert "amount_total" in resp["error"]
        write_calls = [
            c for c in mock_client.execute_kw.call_args_list if c.args[1] == "write"
        ]
        assert write_calls == []

    async def test_create_proceeds_when_no_readonly_field(self) -> None:
        """Sanity: legitimate writes still go through after the pre-flight."""
        mock_client = make_mock_client(execute_kw_return=42)
        gateway = make_gateway(mock_client=mock_client)
        _prime_fields(
            gateway,
            "res.partner",
            {
                "name": FieldInfo(
                    name="name", field_type="char", string="Name", required=True
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": "Acme"})

        assert resp.get("id") == 42

    async def test_pre_flight_swallows_inspector_failure(self) -> None:
        """If ``fields_get`` errors out we fall through to the real create
        rather than blocking a legitimate write.
        """
        mock_client = make_mock_client()
        # First call (fields_get) raises; second call (create) returns id.
        mock_client.execute_kw.side_effect = [
            RuntimeError("offline"),
            7,
        ]
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": "Acme"})

        assert resp.get("id") == 7


# ── P2-7: empty required string ────────────────────────────────────


class TestEmptyRequiredRejected:
    async def test_create_rejects_empty_required_string(self) -> None:
        mock_client = make_mock_client(execute_kw_return=1)
        gateway = make_gateway(mock_client=mock_client)
        _prime_fields(
            gateway,
            "res.partner",
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": "   "})

        assert "error" in resp
        assert "name" in resp["error"]
        assert "empty" in resp["error"].lower()

    async def test_create_rejects_blank_string_required_field(self) -> None:
        mock_client = make_mock_client(execute_kw_return=1)
        gateway = make_gateway(mock_client=mock_client)
        _prime_fields(
            gateway,
            "res.partner",
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                ),
            },
        )
        fn = _get_tool(gateway, "create_record")

        resp = await fn(model="res.partner", values={"name": ""})

        assert "error" in resp
        assert "name" in resp["error"]

    async def test_update_allows_empty_string_on_optional_field(self) -> None:
        """Partial updates that clear an optional string must still pass.

        Empty-required validation only fires on create.
        """
        mock_client = make_mock_client(execute_kw_return=True)
        gateway = make_gateway(mock_client=mock_client)
        _prime_fields(
            gateway,
            "res.partner",
            {
                "comment": FieldInfo(name="comment", field_type="text", string="Notes"),
            },
        )
        fn = _get_tool(gateway, "update_record")

        resp = await fn(
            model="res.partner",
            record_id=1,
            values={"comment": ""},
        )

        assert resp.get("success") is True


# ── P2-11: limit must be > 0 ───────────────────────────────────────


class TestLimitGuards:
    async def test_read_group_rejects_zero_limit(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "read_group")

        resp = await fn(
            model="sale.order",
            fields=["amount_total:sum"],
            groupby=["state"],
            limit=0,
        )

        assert "error" in resp
        assert "positive integer" in resp["error"]
        mock_client.execute_kw.assert_not_called()

    async def test_read_group_rejects_negative_limit(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "read_group")

        resp = await fn(
            model="sale.order",
            fields=["amount_total:sum"],
            groupby=["state"],
            limit=-3,
        )

        assert "error" in resp
        assert "positive integer" in resp["error"]
        mock_client.execute_kw.assert_not_called()

    async def test_read_group_none_limit_uses_default(self) -> None:
        """``limit=None`` keeps the default cap of 500 — the guard fires
        only on explicit ``<= 0`` values.
        """
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)
        fn = _get_tool(gateway, "read_group")

        resp = await fn(
            model="sale.order",
            fields=["amount_total:sum"],
            groupby=["state"],
        )

        assert "error" not in resp
        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["limit"] == 500
