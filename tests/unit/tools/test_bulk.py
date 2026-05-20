"""Tests for bulk_create and bulk_update (ADR-010 Sprint 3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _capture_tools(server: MagicMock) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _tool() -> Any:
        def dec(fn: Any) -> Any:
            captured[fn.__name__] = fn
            return fn

        return dec

    server.tool = _tool
    return captured


@pytest.fixture
def bulk_tools() -> tuple[Any, Any, MagicMock, AsyncMock]:
    """Build (bulk_create, bulk_update, gateway, client) for tests."""
    gw = MagicMock()
    client = AsyncMock()
    client.execute_kw = AsyncMock(return_value=[100, 101, 102])
    mgr = MagicMock()
    mgr.get_active_client = MagicMock(return_value=client)
    mgr.auth_result = MagicMock(is_admin=True, groups=[])
    gw.auth_managers = {"k": mgr}
    gw.restrictions.check_model_access = MagicMock(return_value=None)
    gw.restrictions.check_field_write = MagicMock(return_value=None)
    gw.rbac.sanitize_write_values = MagicMock(side_effect=lambda v, *a, **kw: v)
    gw.rbac.check_tool_access = MagicMock(return_value=None)
    gw.field_inspector.get_fields = AsyncMock(return_value={})
    gw.version_adapter = None
    gw.sanitize_error = lambda exc: str(exc)
    gw.rate_limiter = None
    gw.audit_logger = None
    server = MagicMock()
    captured = _capture_tools(server)
    from odoo_mcp_gateway.tools.bulk import register_bulk_tools

    register_bulk_tools(server, gw)
    return captured["bulk_create"], captured["bulk_update"], gw, client


class TestBulkCreate:
    @pytest.mark.asyncio
    async def test_creates_in_single_chunk(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, client = bulk_tools
        client.execute_kw = AsyncMock(return_value=[100, 101, 102])
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": f"r{i}"} for i in range(3)],
            chunk_size=200,
        )
        assert resp["created_ids"] == [100, 101, 102]
        assert resp["chunks"] == 1
        # Single execute_kw call: one transaction.
        client.execute_kw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_splits_when_above_chunk_size(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, client = bulk_tools
        # 5 records, chunk_size=2 → 3 chunks (2, 2, 1).
        client.execute_kw = AsyncMock(side_effect=[[100, 101], [102, 103], [104]])
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": f"r{i}"} for i in range(5)],
            chunk_size=2,
        )
        assert resp["chunks"] == 3
        assert resp["created_ids"] == [100, 101, 102, 103, 104]
        assert client.execute_kw.await_count == 3

    @pytest.mark.asyncio
    async def test_empty_records_rejected(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, _ = bulk_tools
        resp = await bulk_create(model="res.partner", records=[])
        assert "error" in resp and "empty" in resp["error"]

    @pytest.mark.asyncio
    async def test_non_dict_record_rejected(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, _ = bulk_tools
        resp = await bulk_create(model="res.partner", records=["not a dict"])  # type: ignore[list-item]
        assert "error" in resp

    @pytest.mark.asyncio
    async def test_dry_run_skips_odoo(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, client = bulk_tools
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": "x"}, {"name": "y"}],
            dry_run=True,
        )
        assert resp["dry_run"] is True
        assert resp["chunks"] == 1
        assert resp["total"] == 2
        client.execute_kw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chunk_failure_returns_partial_state(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, client = bulk_tools
        # Chunk 1 succeeds, chunk 2 raises.
        client.execute_kw = AsyncMock(
            side_effect=[[100, 101], RuntimeError("server overload")]
        )
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": f"r{i}"} for i in range(3)],
            chunk_size=2,
        )
        assert "error" in resp
        # Partial-state diagnostic surfaces what DID commit.
        assert resp["partial_ids"] == [100, 101]
        assert resp["completed_chunks"] == 1
        assert resp["total_chunks"] == 2

    @pytest.mark.asyncio
    async def test_above_max_total_rejected(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, _ = bulk_tools
        records = [{"name": f"r{i}"} for i in range(5001)]
        resp = await bulk_create(model="res.partner", records=records)
        assert "error" in resp
        assert "Too many" in resp["error"]


class TestBulkUpdate:
    @pytest.mark.asyncio
    async def test_updates_in_single_chunk(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, _, client = bulk_tools
        client.execute_kw = AsyncMock(return_value=True)
        resp = await bulk_update(
            model="res.partner",
            record_ids=[1, 2, 3],
            values={"name": "renamed"},
        )
        assert resp["updated_count"] == 3
        assert resp["chunks"] == 1
        client.execute_kw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dedupes_record_ids(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, _, client = bulk_tools
        client.execute_kw = AsyncMock(return_value=True)
        # 5 entries but only 3 unique
        resp = await bulk_update(
            model="res.partner",
            record_ids=[1, 1, 2, 2, 3],
            values={"name": "renamed"},
        )
        assert resp["updated_count"] == 3

    @pytest.mark.asyncio
    async def test_chunks_split_ids(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, _, client = bulk_tools
        client.execute_kw = AsyncMock(return_value=True)
        resp = await bulk_update(
            model="res.partner",
            record_ids=list(range(1, 11)),  # 10 ids
            values={"name": "x"},
            chunk_size=4,
        )
        # 10 ids / chunk_size 4 → 3 chunks (4, 4, 2)
        assert resp["chunks"] == 3
        assert resp["updated_count"] == 10
        assert client.execute_kw.await_count == 3

    @pytest.mark.asyncio
    async def test_invalid_id_rejected(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, _, _ = bulk_tools
        resp = await bulk_update(
            model="res.partner",
            record_ids=[1, -2, 3],  # negative id
            values={"name": "x"},
        )
        assert "error" in resp
        assert "positive integer" in resp["error"]

    @pytest.mark.asyncio
    async def test_dry_run_skips_odoo(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, _, client = bulk_tools
        resp = await bulk_update(
            model="res.partner",
            record_ids=[1, 2, 3],
            values={"name": "renamed"},
            dry_run=True,
        )
        assert resp["dry_run"] is True
        assert resp["total_ids"] == 3
        client.execute_kw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chunk_failure_returns_partial_count(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, _, client = bulk_tools
        client.execute_kw = AsyncMock(
            side_effect=[True, RuntimeError("validation failed")]
        )
        resp = await bulk_update(
            model="res.partner",
            record_ids=[1, 2, 3, 4],
            values={"name": "renamed"},
            chunk_size=2,
        )
        assert "error" in resp
        assert resp["updated_count"] == 2  # first chunk committed
        assert resp["completed_chunks"] == 1


class TestSecurityPipeline:
    """Bulk tools MUST run the same security checks as single-record ops."""

    @pytest.mark.asyncio
    async def test_model_restriction_blocks_bulk_create(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, gw, client = bulk_tools
        gw.restrictions.check_model_access = MagicMock(return_value="blocked")
        resp = await bulk_create(
            model="res.users",  # restricted
            records=[{"name": "x"}],
        )
        assert resp["error"] == "blocked"
        client.execute_kw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_field_write_restriction_blocks_bulk_update(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        _, bulk_update, gw, client = bulk_tools
        gw.restrictions.check_field_write = MagicMock(
            side_effect=lambda model, field, is_admin: (
                "password is never writable" if field == "password" else None
            )
        )
        resp = await bulk_update(
            model="res.users",
            record_ids=[1],
            values={"password": "leaked"},
        )
        assert "error" in resp
        assert "password" in resp["error"]
        client.execute_kw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_id_field_rejected(
        self, bulk_tools: tuple[Any, Any, MagicMock, AsyncMock]
    ) -> None:
        bulk_create, _, _, client = bulk_tools
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": "x", "id": 999}],
        )
        assert "error" in resp
        client.execute_kw.assert_not_awaited()
