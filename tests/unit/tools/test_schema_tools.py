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
