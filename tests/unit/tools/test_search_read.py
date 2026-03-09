"""Tests for the search_read tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.client.exceptions import OdooAccessError
from odoo_mcp_gateway.core.discovery.models import FieldInfo
from odoo_mcp_gateway.core.security.config_loader import (
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.crud import register_crud_tools

from .conftest import make_gateway, make_mock_client


def _get_tool(gateway: Any, name: str = "search_read") -> Any:
    server = FastMCP(name="test")
    register_crud_tools(server, gateway)
    for tool_name, tool in server._tool_manager._tools.items():
        if tool_name == name:
            return tool.fn
    raise AssertionError(f"Tool {name!r} not found")


class TestSearchReadBasic:
    async def test_returns_records(self) -> None:
        records = [{"id": 1, "name": "Test"}]
        mock_client = make_mock_client(execute_kw_return=records)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", fields=["name"])

        assert resp["records"] == records
        assert resp["count"] == 1
        assert resp["model"] == "res.partner"

    async def test_with_domain_filter(self) -> None:
        records = [{"id": 2, "name": "Active"}]
        mock_client = make_mock_client(execute_kw_return=records)
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        domain = [["active", "=", True]]
        await fn(model="res.partner", domain=domain, fields=["name"])

        mock_client.execute_kw.assert_called_once()
        call_args = mock_client.execute_kw.call_args
        assert call_args[0][2] == [domain]

    async def test_with_explicit_fields(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name", "email"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["fields"] == ["name", "email"]

    async def test_uses_smart_fields_when_none_specified(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        # Pre-populate field cache
        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                    store=True,
                ),
                "email": FieldInfo(
                    name="email",
                    field_type="char",
                    string="Email",
                    store=True,
                ),
            },
        )

        fn = _get_tool(gateway)
        await fn(model="res.partner")

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert "name" in call_kwargs["fields"]

    async def test_limit_clamping_over_500(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name"], limit=1000)

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["limit"] == 500

    async def test_limit_clamping_negative(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name"], limit=-5)

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["limit"] == 1

    async def test_with_order(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(
            model="res.partner",
            fields=["name"],
            order="create_date desc",
        )

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["order"] == "create_date desc"

    async def test_with_offset(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name"], offset=20)

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["offset"] == 20


class TestSearchReadSecurity:
    async def test_blocked_model_returns_error(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        fn = _get_tool(gateway)
        resp = await fn(model="ir.config_parameter", fields=["key"])

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_admin_only_model_as_non_admin(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                admin_only=["res.users"],
            ),
            is_admin=False,
        )

        fn = _get_tool(gateway)
        resp = await fn(model="res.users", fields=["login"])

        assert "error" in resp
        assert "administrator" in resp["error"]

    async def test_applies_field_filtering(self) -> None:
        records = [
            {"id": 1, "name": "Test", "salary": 50000},
        ]
        rbac_config = RBACConfig(
            sensitive_fields={
                "hr.employee": {
                    "required_group": "hr.group_hr_manager",
                    "fields": ["salary"],
                },
            },
        )
        mock_client = make_mock_client(execute_kw_return=records)
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )

        fn = _get_tool(gateway)
        resp = await fn(
            model="hr.employee",
            fields=["name", "salary"],
        )

        assert resp["records"][0]["salary"] == "***"

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", fields=["name"])

        assert "error" in resp
        assert "Not authenticated" in resp["error"]

    async def test_odoo_access_error(self) -> None:
        mock_client = make_mock_client()
        mock_client.execute_kw = AsyncMock(
            side_effect=OdooAccessError("no permission"),
        )
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", fields=["name"])

        assert "error" in resp
        assert "Access denied" in resp["error"]

    async def test_default_limit(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert call_kwargs["limit"] == 80

    async def test_no_order_not_in_kwargs(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name"])

        call_kwargs = mock_client.execute_kw.call_args[0][3]
        assert "order" not in call_kwargs

    async def test_empty_records_returns_zero_count(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        resp = await fn(model="res.partner", fields=["name"])

        assert resp["count"] == 0
        assert resp["records"] == []

    async def test_default_domain_is_empty_list(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        fn = _get_tool(gateway)
        await fn(model="res.partner", fields=["name"])

        call_args = mock_client.execute_kw.call_args[0][2]
        assert call_args == [[]]


class TestValidateModel:
    """Tests for the _validate_model function in crud.py.

    The model name is used in Odoo RPC calls. Injection or malformed
    names must be rejected, including trailing dots and invalid characters.
    """

    def setup_method(self) -> None:
        from odoo_mcp_gateway.tools.crud import _validate_model

        self._validate_model = _validate_model

    def test_valid_simple_model(self) -> None:
        assert self._validate_model("res.partner") == "res.partner"

    def test_valid_custom_model(self) -> None:
        assert self._validate_model("x_custom.model") == "x_custom.model"

    def test_normalizes_case(self) -> None:
        assert self._validate_model("RES.PARTNER") == "res.partner"

    def test_strips_whitespace(self) -> None:
        assert self._validate_model("  res.partner  ") == "res.partner"

    def test_rejects_empty_string(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid model name"):
            self._validate_model("")

    def test_rejects_whitespace_only(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid model name"):
            self._validate_model("   ")

    def test_rejects_sql_injection(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid model name"):
            self._validate_model("res.partner; DROP TABLE")

    def test_rejects_special_characters(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid model name"):
            self._validate_model("res.partner$evil")

    def test_rejects_leading_digit(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid model name"):
            self._validate_model("1bad.model")

    def test_rejects_too_long_name(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid model name"):
            self._validate_model("a" * 200)


class TestValidateOrder:
    """Tests for the _validate_order function in crud.py.

    The order parameter maps to Odoo's SQL ORDER BY clause, so
    injection or malformed input must be rejected.
    """

    def setup_method(self) -> None:
        from odoo_mcp_gateway.tools.crud import _validate_order

        self._validate_order = _validate_order

    # ── Valid inputs ─────────────────────────────────────────────

    def test_valid_single_field_asc(self) -> None:
        result = self._validate_order("name asc")
        assert result == "name asc"

    def test_valid_single_field_desc(self) -> None:
        result = self._validate_order("name desc")
        assert result == "name desc"

    def test_valid_multi_field(self) -> None:
        result = self._validate_order("name desc, date asc")
        assert result == "name desc, date asc"

    def test_valid_field_no_direction_defaults_to_asc(self) -> None:
        result = self._validate_order("name")
        assert result == "name asc"

    def test_empty_string_returns_empty(self) -> None:
        result = self._validate_order("")
        assert result == ""

    def test_whitespace_only_returns_empty(self) -> None:
        result = self._validate_order("   ")
        assert result == ""

    def test_none_returns_empty(self) -> None:
        """None is handled explicitly by the function."""
        result = self._validate_order(None)
        assert result == ""

    def test_valid_dotted_field(self) -> None:
        result = self._validate_order("partner_id.name asc")
        assert result == "partner_id.name asc"

    def test_valid_underscore_field(self) -> None:
        result = self._validate_order("create_date desc")
        assert result == "create_date desc"

    def test_direction_is_case_insensitive(self) -> None:
        result = self._validate_order("name DESC")
        assert result == "name desc"

    def test_direction_mixed_case(self) -> None:
        result = self._validate_order("name Asc")
        assert result == "name asc"

    # ── SQL injection attempts ───────────────────────────────────

    def test_rejects_semicolon_injection(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name; DROP TABLE users")

    def test_rejects_boolean_injection(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("1=1 --")

    def test_rejects_desc_semicolon_select(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name DESC; SELECT *")

    def test_rejects_union_injection(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name UNION SELECT password FROM users")

    def test_rejects_comment_injection(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name /* comment */")

    # ── Invalid field names ──────────────────────────────────────

    def test_rejects_leading_digit(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("123invalid")

    def test_rejects_double_dash_field(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name--bad")

    def test_rejects_star(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("*")

    def test_rejects_uppercase_field(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("NAME asc")

    def test_rejects_field_with_space(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("na me")

    # ── Invalid directions ───────────────────────────────────────

    def test_rejects_invalid_direction_sideways(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name sideways")

    def test_rejects_invalid_direction_up(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name UP")

    # ── Too many tokens ──────────────────────────────────────────

    def test_rejects_three_tokens(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name desc extra")

    def test_rejects_four_tokens(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            self._validate_order("name desc nulls first")
