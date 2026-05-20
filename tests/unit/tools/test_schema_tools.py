"""Tests for schema inspection tools (list_models, get_model_fields)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.discovery.models import AccessLevel, FieldInfo, ModelInfo
from odoo_mcp_gateway.core.security.config_loader import (
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.tools.schema import register_schema_tools

from .conftest import make_gateway, make_mock_client

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_tools(gateway: Any) -> dict[str, Any]:
    server = FastMCP(name="test")
    register_schema_tools(server, gateway)
    tools: dict[str, Any] = {}
    for name, tool in server._tool_manager._tools.items():
        tools[name] = tool.fn
    return tools


# ------------------------------------------------------------------
# list_models
# ------------------------------------------------------------------


class TestListModels:
    async def test_returns_accessible_models(self) -> None:
        gateway = make_gateway()
        # Pre-populate the registry
        gateway.model_registry._models = {
            "res.partner": ModelInfo(
                name="res.partner",
                description="Contact",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"]()

        assert "models" in resp
        assert resp["count"] >= 1
        found = [m for m in resp["models"] if m["model"] == "res.partner"]
        assert len(found) == 1
        assert found[0]["description"] == "Contact"

    async def test_with_filter(self) -> None:
        gateway = make_gateway()
        gateway.model_registry._models = {
            "res.partner": ModelInfo(
                name="res.partner",
                description="Contact",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
            "sale.order": ModelInfo(
                name="sale.order",
                description="Sales Order",
                is_custom=False,
                is_transient=False,
                module="sale",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"](filter="partner")

        assert resp["count"] == 1
        assert resp["models"][0]["model"] == "res.partner"

    async def test_triggers_discovery_if_needed(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[
                {
                    "model": "res.partner",
                    "name": "Contact",
                    "info": "",
                    "transient": False,
                    "state": "base",
                    "modules": "base",
                },
            ],
        )
        gateway = make_gateway(mock_client=mock_client)
        gateway._models_discovered = False

        tools = _get_tools(gateway)
        await tools["list_models"]()

        assert gateway._models_discovered is True
        mock_client.execute_kw.assert_called()

    async def test_excludes_custom_when_flag_false(self) -> None:
        gateway = make_gateway()
        gateway.model_registry._models = {
            "res.partner": ModelInfo(
                name="res.partner",
                description="Contact",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
            "x_custom.model": ModelInfo(
                name="x_custom.model",
                description="Custom",
                is_custom=True,
                is_transient=False,
                module="custom_mod",
                state="manual",
                access_level=AccessLevel.FULL_CRUD,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"](include_custom=False)

        names = [m["model"] for m in resp["models"]]
        assert "x_custom.model" not in names

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        tools = _get_tools(gateway)
        resp = await tools["list_models"]()

        assert "error" in resp
        assert "Not authenticated" in resp["error"]

    async def test_shows_access_level(self) -> None:
        gateway = make_gateway()
        gateway.model_registry._models = {
            "res.partner": ModelInfo(
                name="res.partner",
                description="Contact",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.READ_ONLY,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"]()

        assert resp["models"][0]["access_level"] == "read_only"

    async def test_response_includes_total_count(self) -> None:
        """UAT MED-2 prep: every response carries an unpaginated ``total``."""
        gateway = make_gateway()
        gateway.model_registry._models = {
            "a.model": ModelInfo(
                name="a.model",
                description="A",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
            "b.model": ModelInfo(
                name="b.model",
                description="B",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"]()
        assert resp["count"] == resp["total"] == 2
        assert "truncated" not in resp

    async def test_compact_mode_returns_name_only(self) -> None:
        """UAT MED-2 — ``compact=True`` strips metadata for small payloads."""
        gateway = make_gateway()
        gateway.model_registry._models = {
            "res.partner": ModelInfo(
                name="res.partner",
                description="Contact",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"](compact=True)

        entry = resp["models"][0]
        assert set(entry.keys()) == {"model"}
        assert entry["model"] == "res.partner"

    async def test_pagination_offset_limit(self) -> None:
        """UAT MED-2 — caller-driven ``offset``/``limit`` paginates the list."""
        gateway = make_gateway()
        gateway.model_registry._models = {
            f"x.model_{i:02d}": ModelInfo(
                name=f"x.model_{i:02d}",
                description=f"Model {i}",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            )
            for i in range(10)
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        page1 = await tools["list_models"](limit=3, offset=0, compact=True)
        page2 = await tools["list_models"](limit=3, offset=3, compact=True)
        page3 = await tools["list_models"](limit=3, offset=6, compact=True)
        page4 = await tools["list_models"](limit=3, offset=9, compact=True)

        # Pages 1-3: truncated, total carries 10.
        for p in (page1, page2, page3):
            assert p["truncated"] is True
            assert p["total"] == 10
            assert p["count"] == 3
        # Page 4: only 1 item, last page → not truncated.
        assert page4["count"] == 1
        assert page4.get("truncated") is None or page4.get("truncated") is False

        # Pages don't overlap and together cover all 10.
        seen = {m["model"] for p in (page1, page2, page3, page4) for m in p["models"]}
        assert len(seen) == 10

    async def test_invalid_limit_and_offset_rejected(self) -> None:
        gateway = make_gateway()
        gateway.model_registry._models = {}
        gateway._models_discovered = True
        tools = _get_tools(gateway)

        resp_bad_limit = await tools["list_models"](limit=0)
        assert "error" in resp_bad_limit
        resp_bad_offset = await tools["list_models"](offset=-5)
        assert "error" in resp_bad_offset

    async def test_auto_truncate_above_soft_cap(self) -> None:
        """UAT MED-2 — large model catalogs auto-truncate with a hint."""
        # 5000 entries × 200 byte est = 1_000_000 > 750_000 cap.
        gateway = make_gateway()
        gateway.model_registry._models = {
            f"big.model_{i:05d}": ModelInfo(
                name=f"big.model_{i:05d}",
                description="x" * 100,
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.FULL_CRUD,
            )
            for i in range(5000)
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"]()
        # Auto-truncated, hint surfaced.
        assert resp["truncated"] is True
        assert resp["total"] == 5000
        assert resp["count"] < 5000
        assert "hint" in resp
        assert "compact=true" in resp["hint"] or "limit/offset" in resp["hint"]

    async def test_admin_sees_admin_only_models(self) -> None:
        gateway = make_gateway(is_admin=True)
        gateway.model_registry._models = {
            "ir.model": ModelInfo(
                name="ir.model",
                description="Models",
                is_custom=False,
                is_transient=False,
                module="base",
                state="base",
                access_level=AccessLevel.ADMIN_ONLY,
            ),
        }
        gateway._models_discovered = True

        tools = _get_tools(gateway)
        resp = await tools["list_models"]()

        assert resp["count"] == 1
        assert resp["models"][0]["model"] == "ir.model"


# ------------------------------------------------------------------
# get_model_fields
# ------------------------------------------------------------------


class TestGetModelFields:
    async def test_returns_field_info(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        # Mock the field inspector
        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                ),
                "email": FieldInfo(
                    name="email",
                    field_type="char",
                    string="Email",
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](model="res.partner")

        assert "fields" in resp
        assert "name" in resp["fields"]
        assert resp["fields"]["name"]["type"] == "char"
        assert resp["fields"]["name"]["required"] is True

    async def test_with_field_filter(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(name="name", field_type="char", string="Name"),
                "email": FieldInfo(name="email", field_type="char", string="Email"),
                "phone": FieldInfo(name="phone", field_type="char", string="Phone"),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter="email",
        )

        assert "email" in resp["fields"]
        assert "name" not in resp["fields"]
        assert "phone" not in resp["fields"]

    async def test_restricted_model_returns_error(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](model="ir.config_parameter")

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_applies_rbac_field_filtering(self) -> None:
        rbac_config = RBACConfig(
            sensitive_fields={
                "res.partner": {
                    "required_group": "hr.group_hr_manager",
                    "fields": ["bank_ids"],
                },
            },
        )
        mock_client = make_mock_client()
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )

        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(name="name", field_type="char", string="Name"),
                "bank_ids": FieldInfo(
                    name="bank_ids",
                    field_type="one2many",
                    string="Banks",
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](model="res.partner")

        assert "bank_ids" not in resp["fields"]
        assert "name" in resp["fields"]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](model="res.partner")

        assert "error" in resp

    async def test_exclude_readonly(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    readonly=False,
                ),
                "display_name": FieldInfo(
                    name="display_name",
                    field_type="char",
                    string="Display Name",
                    readonly=True,
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            include_readonly=False,
        )

        assert "name" in resp["fields"]
        assert "display_name" not in resp["fields"]

    async def test_returns_model_and_count(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "name": FieldInfo(name="name", field_type="char", string="Order Ref"),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](model="sale.order")

        assert resp["model"] == "sale.order"
        assert resp["count"] == 1


# ------------------------------------------------------------------
# UAT MED-1 (Odoo 17) — field_filter accepts list or CSV string
# ------------------------------------------------------------------


class TestGetModelFieldsFilterListShapes:
    """Verify ``field_filter`` accepts CSV strings, lists, and single names.

    The Odoo 17 UAT caught that the old code substring-matched against
    field names AND labels, so ``"name,phone,email"`` (a CSV) matched
    nothing — no field name contains a comma. The current contract is:

    * CSV or list   → exact-name match (subset returned)
    * Single token  → legacy substring match (back-compat)
    * Empty / None  → no filter
    """

    @staticmethod
    def _gateway_with_partner_fields() -> Any:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)
        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(name="name", field_type="char", string="Name"),
                "phone": FieldInfo(name="phone", field_type="char", string="Phone"),
                "email": FieldInfo(name="email", field_type="char", string="Email"),
                "is_company": FieldInfo(
                    name="is_company", field_type="boolean", string="Is Company"
                ),
                "country_id": FieldInfo(
                    name="country_id", field_type="many2one", string="Country"
                ),
                "vat": FieldInfo(name="vat", field_type="char", string="VAT"),
            },
        )
        return gateway

    async def test_csv_string_returns_named_subset(self) -> None:
        gateway = self._gateway_with_partner_fields()
        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter="name,phone,email,is_company,country_id",
        )
        assert set(resp["fields"].keys()) == {
            "name",
            "phone",
            "email",
            "is_company",
            "country_id",
        }
        assert resp["count"] == 5
        # The unrequested field MUST NOT appear.
        assert "vat" not in resp["fields"]

    async def test_list_of_names_returns_subset(self) -> None:
        gateway = self._gateway_with_partner_fields()
        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter=["name", "country_id"],
        )
        assert set(resp["fields"].keys()) == {"name", "country_id"}

    async def test_single_token_substring_legacy_behaviour(self) -> None:
        """Single-token strings keep the legacy substring match."""
        gateway = self._gateway_with_partner_fields()
        tools = _get_tools(gateway)
        # "company" is a substring of "is_company" — substring match wins.
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter="company",
        )
        assert "is_company" in resp["fields"]
        assert "name" not in resp["fields"]

    async def test_empty_string_returns_all(self) -> None:
        gateway = self._gateway_with_partner_fields()
        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter="",
        )
        # All six accessible fields visible.
        assert resp["count"] == 6

    async def test_none_returns_all(self) -> None:
        gateway = self._gateway_with_partner_fields()
        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter=None,
        )
        assert resp["count"] == 6

    async def test_nonexistent_names_silently_dropped(self) -> None:
        """Tokens for fields that don't exist on the model are simply ignored.

        No error is raised; the response contains the subset that does
        exist. Empty if none of the supplied names exist.
        """
        gateway = self._gateway_with_partner_fields()
        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter="name,no_such_field,country_id,also_missing",
        )
        assert set(resp["fields"].keys()) == {"name", "country_id"}

    async def test_filter_intersects_with_rbac_redaction(self) -> None:
        """Caller-supplied name MUST NOT bypass RBAC field redaction."""
        rbac_config = RBACConfig(
            sensitive_fields={
                "res.partner": {
                    "required_group": "hr.group_hr_manager",
                    "fields": ["vat"],
                },
            },
        )
        mock_client = make_mock_client()
        gateway = make_gateway(
            rbac_config=rbac_config,
            mock_client=mock_client,
            user_groups=["base.group_user"],
        )
        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(name="name", field_type="char", string="Name"),
                "vat": FieldInfo(name="vat", field_type="char", string="VAT"),
            },
        )
        tools = _get_tools(gateway)
        resp = await tools["get_model_fields"](
            model="res.partner",
            field_filter="name,vat",
        )
        # ``vat`` is redacted by RBAC even though the caller asked for it.
        assert "name" in resp["fields"]
        assert "vat" not in resp["fields"]
