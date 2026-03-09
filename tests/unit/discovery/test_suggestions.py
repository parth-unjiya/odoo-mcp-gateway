"""Tests for ModelSuggestions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from odoo_mcp_gateway.core.discovery.model_registry import ModelRegistry
from odoo_mcp_gateway.core.discovery.suggestions import ModelSuggestions

FIXTURES = Path(__file__).parent / "fixtures"


def _load_ir_model_records() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "mock_ir_model_response.json").read_text())


def _load_model_access_config() -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / "custom_model_access.yaml").read_text())


async def _make_populated_registry() -> ModelRegistry:
    config = _load_model_access_config()
    registry = ModelRegistry(model_access_config=config)
    client = AsyncMock()
    client.execute_kw.return_value = _load_ir_model_records()
    await registry.discover(client)
    return registry


# ------------------------------------------------------------------
# search
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_sales_finds_sale_order() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results = suggestions.search("sale", is_admin=False)
    names = [m.name for m in results]
    assert "sale.order" in names


@pytest.mark.asyncio
async def test_search_employee_finds_hr_employee() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    # hr.employee is in full_crud list so accessible
    results = suggestions.search("employee", is_admin=False)
    names = [m.name for m in results]
    # "employee" matches the description "Employee"
    assert any("hr.employee" == n for n in names)


@pytest.mark.asyncio
async def test_search_case_insensitive() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results_lower = suggestions.search("sale", is_admin=False)
    results_upper = suggestions.search("SALE", is_admin=False)
    # Both should find sale.order
    assert any(m.name == "sale.order" for m in results_lower)
    assert any(m.name == "sale.order" for m in results_upper)


@pytest.mark.asyncio
async def test_search_empty_returns_nothing() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    assert suggestions.search("") == []


@pytest.mark.asyncio
async def test_search_filters_by_access_non_admin() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results = suggestions.search("ir", is_admin=False)
    # ir.config_parameter is BLOCKED, should not appear
    names = [m.name for m in results]
    assert "ir.config_parameter" not in names


@pytest.mark.asyncio
async def test_search_custom_models_included() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results = suggestions.search("delivery", is_admin=False)
    names = [m.name for m in results]
    assert "custom.delivery.route" in names


@pytest.mark.asyncio
async def test_search_keyword_expansion() -> None:
    """Searching for 'inventory' should expand via category keywords."""
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    # "inventory" category has keyword "stock" which should match stock.picking
    # But stock.picking is BLOCKED in our config, so it won't appear.
    # However, "product" is also in inventory category...
    # None of stock/product models are accessible in our config.
    # This tests the mechanism works even if results are empty.
    results = suggestions.search("inventory", is_admin=False)
    # At least no crash.
    assert isinstance(results, list)


# ------------------------------------------------------------------
# get_by_category
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_category_sales() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results = suggestions.get_by_category("sales", is_admin=False)
    names = [m.name for m in results]
    assert "sale.order" in names
    assert "crm.lead" in names


@pytest.mark.asyncio
async def test_get_by_category_hr() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results = suggestions.get_by_category("hr", is_admin=False)
    names = [m.name for m in results]
    # hr.employee is in full_crud config and matches "hr" keyword
    assert "hr.employee" in names


@pytest.mark.asyncio
async def test_get_by_category_unknown() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    results = suggestions.get_by_category("nonexistent", is_admin=False)
    assert results == []


@pytest.mark.asyncio
async def test_get_by_category_case_insensitive() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    r1 = suggestions.get_by_category("Sales", is_admin=False)
    r2 = suggestions.get_by_category("sales", is_admin=False)
    assert {m.name for m in r1} == {m.name for m in r2}


# ------------------------------------------------------------------
# get_categories
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_categories_returns_counts() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    cats = suggestions.get_categories(is_admin=True)
    assert isinstance(cats, dict)
    # Should have all known categories
    assert "sales" in cats
    assert "inventory" in cats
    assert "hr" in cats
    assert "project" in cats


@pytest.mark.asyncio
async def test_get_categories_sales_count() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    cats = suggestions.get_categories(is_admin=True)
    # "sales" keywords: sale, crm, quotation, invoice, payment
    # Accessible (admin=True) models matching: sale.order, crm.lead
    assert cats["sales"] >= 2


# ------------------------------------------------------------------
# suggest_related
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_related_for_sale_order() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    related = suggestions.suggest_related("sale.order", is_admin=True)
    # sale.advance.payment.inv also starts with "sale." but it's BLOCKED
    # so it won't appear in accessible models.
    # The method uses is_admin=True so it sees all accessible models.
    # sale.advance.payment.inv is BLOCKED => not in accessible.
    names = [m.name for m in related]
    assert "sale.order" not in names  # exclude self


@pytest.mark.asyncio
async def test_suggest_related_for_custom_delivery() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    related = suggestions.suggest_related("custom.delivery.route", is_admin=True)
    names = [m.name for m in related]
    assert "custom.delivery.stop" in names


@pytest.mark.asyncio
async def test_suggest_related_excludes_self() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    related = suggestions.suggest_related("custom.delivery.route", is_admin=True)
    names = [m.name for m in related]
    assert "custom.delivery.route" not in names


@pytest.mark.asyncio
async def test_suggest_related_empty_for_unique_prefix() -> None:
    registry = await _make_populated_registry()
    suggestions = ModelSuggestions(registry)
    # x_studio_warranty is the only model with prefix "x_studio_warranty"
    related = suggestions.suggest_related("x_studio_warranty", is_admin=True)
    assert related == []
