"""Tests for FieldInspector."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from odoo_mcp_gateway.core.discovery.field_inspector import FieldInspector
from odoo_mcp_gateway.core.discovery.models import FieldInfo

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fields_response() -> dict[str, Any]:
    return json.loads((FIXTURES / "mock_fields_get_response.json").read_text())


def _make_client(response: dict[str, Any] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.execute_kw.return_value = response or _load_fields_response()
    return client


# ------------------------------------------------------------------
# get_fields — basic
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fields_calls_fields_get() -> None:
    client = _make_client()
    inspector = FieldInspector()
    await inspector.get_fields(client, "sale.order")

    client.execute_kw.assert_called_once_with(
        "sale.order",
        "fields_get",
        [],
        {
            "attributes": [
                "type",
                "string",
                "required",
                "readonly",
                "store",
                "relation",
                "selection",
                "help",
            ]
        },
    )


@pytest.mark.asyncio
async def test_field_info_attributes() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    name_field = fields["name"]
    assert name_field.name == "name"
    assert name_field.field_type == "char"
    assert name_field.string == "Order Reference"
    assert name_field.required is True
    assert name_field.readonly is False
    assert name_field.store is True


@pytest.mark.asyncio
async def test_relational_field_has_relation() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    partner = fields["partner_id"]
    assert partner.relation == "res.partner"
    assert partner.field_type == "many2one"


@pytest.mark.asyncio
async def test_binary_field_detected() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    image = fields["image_1920"]
    assert image.is_binary is True
    assert image.field_type == "binary"


@pytest.mark.asyncio
async def test_selection_field_has_options() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    state = fields["state"]
    assert state.field_type == "selection"
    assert ("draft", "Quotation") in state.selection
    assert ("sale", "Sales Order") in state.selection
    assert len(state.selection) == 4


@pytest.mark.asyncio
async def test_non_relational_field_relation_is_none() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    assert fields["name"].relation is None


# ------------------------------------------------------------------
# Caching
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_on_second_call() -> None:
    client = _make_client()
    inspector = FieldInspector()
    await inspector.get_fields(client, "sale.order")
    await inspector.get_fields(client, "sale.order")
    # Only one actual call should be made
    assert client.execute_kw.call_count == 1


@pytest.mark.asyncio
async def test_cache_miss_after_ttl() -> None:
    client = _make_client()
    inspector = FieldInspector(cache_ttl=1)

    await inspector.get_fields(client, "sale.order")
    assert client.execute_kw.call_count == 1

    # Simulate TTL expiration by patching time.monotonic
    original_monotonic = time.monotonic
    with patch(
        "odoo_mcp_gateway.core.discovery.field_inspector.time.monotonic",
        side_effect=lambda: original_monotonic() + 2,
    ):
        await inspector.get_fields(client, "sale.order")
    assert client.execute_kw.call_count == 2


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache() -> None:
    client = _make_client()
    inspector = FieldInspector()
    await inspector.get_fields(client, "sale.order")
    await inspector.get_fields(client, "sale.order", force_refresh=True)
    assert client.execute_kw.call_count == 2


@pytest.mark.asyncio
async def test_invalidate_cache_specific_model() -> None:
    client = _make_client()
    inspector = FieldInspector()
    await inspector.get_fields(client, "sale.order")
    inspector.invalidate_cache("sale.order")
    await inspector.get_fields(client, "sale.order")
    assert client.execute_kw.call_count == 2


@pytest.mark.asyncio
async def test_invalidate_cache_all() -> None:
    client = _make_client()
    inspector = FieldInspector()
    await inspector.get_fields(client, "sale.order")
    inspector.invalidate_cache()
    await inspector.get_fields(client, "sale.order")
    assert client.execute_kw.call_count == 2


@pytest.mark.asyncio
async def test_invalidate_nonexistent_model_no_error() -> None:
    inspector = FieldInspector()
    inspector.invalidate_cache("nonexistent.model")  # should not raise


@pytest.mark.asyncio
async def test_different_models_cached_independently() -> None:
    client = AsyncMock()
    resp1 = {
        "name": {
            "type": "char",
            "string": "Name",
            "required": True,
            "readonly": False,
            "store": True,
        }
    }
    resp2 = {
        "code": {
            "type": "char",
            "string": "Code",
            "required": False,
            "readonly": False,
            "store": True,
        }
    }
    client.execute_kw.side_effect = [resp1, resp2]

    inspector = FieldInspector()
    f1 = await inspector.get_fields(client, "model.a")
    f2 = await inspector.get_fields(client, "model.b")
    assert "name" in f1
    assert "code" in f2
    assert client.execute_kw.call_count == 2


# ------------------------------------------------------------------
# get_important_fields
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_important_fields_includes_name() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "name" in important


@pytest.mark.asyncio
async def test_important_fields_includes_state() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "state" in important


@pytest.mark.asyncio
async def test_important_fields_includes_required() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "partner_id" in important
    assert "date_order" in important


@pytest.mark.asyncio
async def test_important_fields_excludes_binary() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "image_1920" not in important


@pytest.mark.asyncio
async def test_important_fields_excludes_one2many() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "order_line" not in important


@pytest.mark.asyncio
async def test_important_fields_excludes_internal() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "__last_update" not in important
    assert "write_uid" not in important
    assert "create_date" not in important


@pytest.mark.asyncio
async def test_important_fields_max_25() -> None:
    # Build a model with 40 stored, required, non-internal fields.
    big_fields: dict[str, FieldInfo] = {}
    for i in range(40):
        fname = f"field_{i}"
        big_fields[fname] = FieldInfo(
            name=fname,
            field_type="char",
            string=f"Field {i}",
            required=True,
            store=True,
        )
    inspector = FieldInspector()
    important = inspector.get_important_fields("big.model", big_fields)
    assert len(important) <= 25


@pytest.mark.asyncio
async def test_important_fields_includes_key_relations() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "user_id" in important
    assert "company_id" in important


@pytest.mark.asyncio
async def test_important_fields_includes_monetary() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert "amount_total" in important


@pytest.mark.asyncio
async def test_important_fields_no_duplicates() -> None:
    inspector = FieldInspector()
    fields = await inspector.get_fields(_make_client(), "sale.order")
    important = inspector.get_important_fields("sale.order", fields)
    assert len(important) == len(set(important))
