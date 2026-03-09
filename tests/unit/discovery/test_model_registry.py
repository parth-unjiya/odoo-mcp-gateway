"""Tests for ModelRegistry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from odoo_mcp_gateway.core.discovery.model_registry import ModelRegistry
from odoo_mcp_gateway.core.discovery.models import AccessLevel

FIXTURES = Path(__file__).parent / "fixtures"


def _load_ir_model_records() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "mock_ir_model_response.json").read_text())


def _load_model_access_config() -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / "custom_model_access.yaml").read_text())


def _make_client(records: list[dict[str, Any]] | None = None) -> AsyncMock:
    client = AsyncMock()
    data = records if records is not None else _load_ir_model_records()
    client.execute_kw.return_value = data
    return client


def _make_registry(
    config: dict[str, Any] | None = None,
    blocked: list[str] | None = None,
) -> ModelRegistry:
    cfg = config if config is not None else _load_model_access_config()
    return ModelRegistry(model_access_config=cfg, blocked_models=blocked)


# ------------------------------------------------------------------
# discover()
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_calls_execute_kw_correctly() -> None:
    client = _make_client()
    registry = _make_registry()
    await registry.discover(client)

    client.execute_kw.assert_called_once_with(
        "ir.model",
        "search_read",
        [[]],
        {
            "fields": ["model", "name", "info", "transient", "state", "modules"],
            "limit": 0,
        },
    )


@pytest.mark.asyncio
async def test_discover_populates_models() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.get_model("res.partner") is not None
    assert registry.get_model("sale.order") is not None


# ------------------------------------------------------------------
# Stock model classification
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stock_model_res_partner() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("res.partner")
    assert m is not None
    assert m.is_custom is False


@pytest.mark.asyncio
async def test_stock_model_sale_order() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("sale.order")
    assert m is not None
    assert m.is_custom is False
    assert m.description == "Sales Order"


# ------------------------------------------------------------------
# Custom model classification
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_model_classification() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("custom.delivery.route")
    assert m is not None
    assert m.is_custom is True


@pytest.mark.asyncio
async def test_manual_studio_model_is_custom() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("x_studio_warranty")
    assert m is not None
    assert m.is_custom is True
    assert m.state == "manual"


# ------------------------------------------------------------------
# Transient model detection
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_model_detected() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("sale.advance.payment.inv")
    assert m is not None
    assert m.is_transient is True


@pytest.mark.asyncio
async def test_non_transient_model() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("res.partner")
    assert m is not None
    assert m.is_transient is False


# ------------------------------------------------------------------
# Access levels from config
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_level_full_crud() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("sale.order")
    assert m is not None
    assert m.access_level == AccessLevel.FULL_CRUD


@pytest.mark.asyncio
async def test_access_level_read_only() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("res.company")
    assert m is None  # res.company not in mock data; use stock.picking instead
    # Use a model that IS in the data but listed as read_only
    # Neither res.company nor res.country are in mock records. Instead,
    # test via x_studio_warranty which is in custom read_only.
    m2 = registry.get_model("x_studio_warranty")
    assert m2 is not None
    assert m2.access_level == AccessLevel.READ_ONLY


@pytest.mark.asyncio
async def test_access_level_admin_only() -> None:
    # ir.model is in admin_only config AND in mock data (id 10 is ir.config_parameter).
    # We need ir.model in mock data to test. It is NOT in the fixture,
    # but ir.config_parameter IS in the base module. Let's add ir.model.
    records = _load_ir_model_records()
    records.append(
        {
            "id": 20,
            "model": "ir.model",
            "name": "Model",
            "transient": False,
            "state": "base",
            "modules": "base",
            "info": "",
        }
    )
    registry = _make_registry()
    await registry.discover(_make_client(records))
    m = registry.get_model("ir.model")
    assert m is not None
    assert m.access_level == AccessLevel.ADMIN_ONLY


@pytest.mark.asyncio
async def test_default_deny_unlisted_admin_only() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    # Unlisted models under deny policy are ADMIN_ONLY (not BLOCKED),
    # consistent with RestrictionChecker which lets admin through.
    m = registry.get_model("ir.config_parameter")
    assert m is not None
    assert m.access_level == AccessLevel.ADMIN_ONLY


@pytest.mark.asyncio
async def test_default_allow_policy() -> None:
    config = _load_model_access_config()
    config["default_policy"] = "allow"
    registry = _make_registry(config=config)
    await registry.discover(_make_client())
    # ir.config_parameter is not explicitly listed but default is allow
    m = registry.get_model("ir.config_parameter")
    assert m is not None
    assert m.access_level == AccessLevel.FULL_CRUD


@pytest.mark.asyncio
async def test_blocked_models_override() -> None:
    registry = _make_registry(blocked=["sale.order"])
    await registry.discover(_make_client())
    m = registry.get_model("sale.order")
    assert m is not None
    assert m.access_level == AccessLevel.BLOCKED


# ------------------------------------------------------------------
# get_model
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_returns_correct_info() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("crm.lead")
    assert m is not None
    assert m.name == "crm.lead"
    assert m.description == "Lead/Opportunity"
    assert m.module == "crm"


@pytest.mark.asyncio
async def test_get_model_returns_none_for_unknown() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.get_model("totally.unknown.model") is None


# ------------------------------------------------------------------
# get_accessible_models
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_accessible_models_non_admin() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    models = registry.get_accessible_models(is_admin=False)
    levels = {m.access_level for m in models}
    assert AccessLevel.ADMIN_ONLY not in levels
    assert AccessLevel.BLOCKED not in levels
    # Should include full_crud and read_only models
    names = {m.name for m in models}
    assert "sale.order" in names
    assert "x_studio_warranty" in names  # read_only custom


@pytest.mark.asyncio
async def test_get_accessible_models_admin_includes_admin_only() -> None:
    records = _load_ir_model_records()
    records.append(
        {
            "id": 20,
            "model": "ir.model",
            "name": "Model",
            "transient": False,
            "state": "base",
            "modules": "base",
            "info": "",
        }
    )
    registry = _make_registry()
    await registry.discover(_make_client(records))
    models = registry.get_accessible_models(is_admin=True)
    names = {m.name for m in models}
    assert "ir.model" in names
    assert "sale.order" in names


# ------------------------------------------------------------------
# search_models
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_models_by_name() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    results = registry.search_models("sale")
    names = [m.name for m in results]
    assert "sale.order" in names


@pytest.mark.asyncio
async def test_search_models_by_description() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    results = registry.search_models("Contact")
    names = [m.name for m in results]
    assert "res.partner" in names


@pytest.mark.asyncio
async def test_search_models_case_insensitive() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    results = registry.search_models("SALE")
    names = [m.name for m in results]
    assert "sale.order" in names


@pytest.mark.asyncio
async def test_search_models_empty_query() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.search_models("") == []


# ------------------------------------------------------------------
# is_custom_model
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_custom_model_true() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.is_custom_model("custom.delivery.route") is True


@pytest.mark.asyncio
async def test_is_custom_model_false_for_stock() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.is_custom_model("sale.order") is False


@pytest.mark.asyncio
async def test_is_custom_model_false_for_unknown() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.is_custom_model("nonexistent.model") is False


# ------------------------------------------------------------------
# _is_stock_module
# ------------------------------------------------------------------


def test_is_stock_module_exact_match() -> None:
    registry = _make_registry()
    assert registry._is_stock_module("base") is True
    assert registry._is_stock_module("sale") is True
    assert registry._is_stock_module("crm") is True


def test_is_stock_module_prefix_match() -> None:
    registry = _make_registry()
    assert registry._is_stock_module("l10n_us") is True
    assert registry._is_stock_module("sale_stock") is True
    assert registry._is_stock_module("web_editor") is True


def test_is_stock_module_non_stock() -> None:
    registry = _make_registry()
    assert registry._is_stock_module("custom_delivery") is False
    assert registry._is_stock_module("studio_customization") is False


# ------------------------------------------------------------------
# Module field parsing
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_module_field_comma_separated() -> None:
    records = [
        {
            "id": 99,
            "model": "sale.order",
            "name": "Sales Order",
            "transient": False,
            "state": "base",
            "modules": "sale, sale_stock, sale_management",
            "info": "",
        },
    ]
    config = {"default_policy": "allow"}
    registry = _make_registry(config=config)
    await registry.discover(_make_client(records))
    m = registry.get_model("sale.order")
    assert m is not None
    assert m.module == "sale"
    assert m.is_custom is False


@pytest.mark.asyncio
async def test_model_custom_when_all_modules_non_stock() -> None:
    records = [
        {
            "id": 100,
            "model": "custom.model",
            "name": "Custom Model",
            "transient": False,
            "state": "base",
            "modules": "custom_mod_a, custom_mod_b",
            "info": "",
        },
    ]
    config = {"default_policy": "allow"}
    registry = _make_registry(config=config)
    await registry.discover(_make_client(records))
    m = registry.get_model("custom.model")
    assert m is not None
    assert m.is_custom is True


@pytest.mark.asyncio
async def test_model_not_custom_when_any_module_is_stock() -> None:
    records = [
        {
            "id": 101,
            "model": "hybrid.model",
            "name": "Hybrid",
            "transient": False,
            "state": "base",
            "modules": "custom_addon, sale",
            "info": "",
        },
    ]
    config = {"default_policy": "allow"}
    registry = _make_registry(config=config)
    await registry.discover(_make_client(records))
    m = registry.get_model("hybrid.model")
    assert m is not None
    assert m.is_custom is False


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_clears_previous_models() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    assert registry.get_model("res.partner") is not None

    # Discover again with empty records
    await registry.discover(_make_client([]))
    assert registry.get_model("res.partner") is None


def test_empty_registry() -> None:
    registry = _make_registry()
    assert registry.get_model("anything") is None
    assert registry.get_accessible_models() == []
    assert registry.search_models("test") == []


@pytest.mark.asyncio
async def test_custom_model_full_crud_access_from_custom_config() -> None:
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("custom.delivery.route")
    assert m is not None
    assert m.access_level == AccessLevel.FULL_CRUD


@pytest.mark.asyncio
async def test_stock_picking_not_in_config() -> None:
    # stock.picking is NOT in our test config. Under deny policy,
    # unlisted models are ADMIN_ONLY (accessible to admins only).
    registry = _make_registry()
    await registry.discover(_make_client())
    m = registry.get_model("stock.picking")
    assert m is not None
    assert m.access_level == AccessLevel.ADMIN_ONLY
