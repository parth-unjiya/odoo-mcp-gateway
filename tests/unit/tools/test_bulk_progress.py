"""Tests for progress notifications on bulk_create / bulk_update (Sprint 5)."""

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
def bulk_with_ctx() -> tuple[Any, Any, AsyncMock]:
    """Build bulk_create + bulk_update with a stubbed ctx that records progress."""
    gw = MagicMock()
    client = AsyncMock()
    client.execute_kw = AsyncMock(return_value=[100, 101])
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

    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return captured["bulk_create"], captured["bulk_update"], ctx


class TestBulkCreateProgress:
    @pytest.mark.asyncio
    async def test_progress_reported_per_chunk(
        self, bulk_with_ctx: tuple[Any, Any, AsyncMock]
    ) -> None:
        bulk_create, _, ctx = bulk_with_ctx
        # 5 records, chunk_size 2 → 3 chunks (sizes 2, 2, 1).
        await bulk_create(
            model="res.partner",
            records=[{"name": f"r{i}"} for i in range(5)],
            chunk_size=2,
            ctx=ctx,
        )
        # Three progress notifications, one per chunk.
        assert ctx.report_progress.await_count == 3

    @pytest.mark.asyncio
    async def test_no_ctx_no_progress(
        self, bulk_with_ctx: tuple[Any, Any, AsyncMock]
    ) -> None:
        bulk_create, _, _ = bulk_with_ctx
        # Without a ctx, no progress channel is exercised — silent success.
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": "x"}],
        )
        assert "created_ids" in resp

    @pytest.mark.asyncio
    async def test_progress_failure_does_not_abort_tool(
        self, bulk_with_ctx: tuple[Any, Any, AsyncMock]
    ) -> None:
        """A broken progress channel must NOT crash the bulk op."""
        bulk_create, _, ctx = bulk_with_ctx
        ctx.report_progress = AsyncMock(side_effect=RuntimeError("channel broken"))
        resp = await bulk_create(
            model="res.partner",
            records=[{"name": "x"}],
            ctx=ctx,
        )
        assert "created_ids" in resp


class TestBulkUpdateProgress:
    @pytest.mark.asyncio
    async def test_progress_reported_per_chunk(
        self, bulk_with_ctx: tuple[Any, Any, AsyncMock]
    ) -> None:
        _, bulk_update, ctx = bulk_with_ctx
        # The fixture's client returns a list for bulk_update's write,
        # which Odoo would normally answer with True — our update tool
        # doesn't care about the shape because it tracks ``completed``
        # by chunk size, not by return value.
        await bulk_update(
            model="res.partner",
            record_ids=list(range(1, 11)),
            values={"name": "renamed"},
            chunk_size=4,
            ctx=ctx,
        )
        # 10 ids / 4 = 3 chunks → 3 progress notifications.
        assert ctx.report_progress.await_count == 3
