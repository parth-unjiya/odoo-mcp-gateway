"""Regression tests for execute_method dry_run sanitisation echo (H4).

Before v0.2.2-final, ``execute_method(dry_run=True)`` returned a minimal
``{model, method, record_ids, args_count}`` response. The real call
silently stripped ``_DANGEROUS_CONTEXT_KEYS`` from ``kwargs["context"]``,
but the dry_run preview didn't surface that — callers couldn't tell
that their ``context={'tracking_disable': True}`` would be dropped.

The fix echoes ``sanitized_kwargs`` (post-filter) and a
``stripped_context_keys`` list so the dry_run preview matches what
real mode would actually dispatch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.tools.crud import register_crud_tools


def _capture_tools(server: MagicMock) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _tool() -> Any:
        def decorator(fn: Any) -> Any:
            captured[fn.__name__] = fn
            return fn

        return decorator

    server.tool = _tool
    return captured


@pytest.fixture
def execute_method_fn() -> Any:
    gw = MagicMock()
    client = AsyncMock()
    client.execute_kw = AsyncMock(return_value=True)
    mgr = MagicMock()
    mgr.get_active_client = MagicMock(return_value=client)
    mgr.auth_result = MagicMock(is_admin=True)
    gw.auth_managers = {"k": mgr}
    gw.restrictions.check_model_access = MagicMock(return_value=None)
    gw.restrictions.check_method_access = MagicMock(return_value=None)
    gw.field_inspector.get_fields = AsyncMock(return_value={})
    gw.version_adapter = None
    gw.sanitize_error = lambda exc: str(exc)
    gw.rate_limiter = None
    gw.rbac = None  # security_gate skips when None
    gw.audit_logger = None
    server = MagicMock()
    captured = _capture_tools(server)
    register_crud_tools(server, gw)
    return captured["execute_method"], client


class TestDryRunEcho:
    async def test_dry_run_echoes_sanitized_kwargs(
        self, execute_method_fn: tuple[Any, AsyncMock]
    ) -> None:
        execute_method, client = execute_method_fn
        resp = await execute_method(
            model="sale.order",
            method="action_confirm",
            record_ids=[1, 2],
            args=[],
            kwargs={"context": {"lang": "en_US", "tracking_disable": True}},
            dry_run=True,
        )
        assert resp["dry_run"] is True
        # tracking_disable is in _DANGEROUS_CONTEXT_KEYS — must be stripped
        assert resp["sanitized_kwargs"]["context"] == {"lang": "en_US"}
        assert "stripped_context_keys" in resp
        assert "tracking_disable" in resp["stripped_context_keys"]
        # No real Odoo call in dry_run mode
        client.execute_kw.assert_not_awaited()

    async def test_dry_run_omits_stripped_list_when_nothing_stripped(
        self, execute_method_fn: tuple[Any, AsyncMock]
    ) -> None:
        execute_method, _client = execute_method_fn
        resp = await execute_method(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
            args=[],
            kwargs={"context": {"lang": "fr_FR"}},
            dry_run=True,
        )
        assert resp["dry_run"] is True
        assert resp["sanitized_kwargs"]["context"] == {"lang": "fr_FR"}
        assert "stripped_context_keys" not in resp

    async def test_dry_run_handles_no_kwargs(
        self, execute_method_fn: tuple[Any, AsyncMock]
    ) -> None:
        execute_method, _client = execute_method_fn
        resp = await execute_method(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
            args=None,
            kwargs=None,
            dry_run=True,
        )
        assert resp["dry_run"] is True
        # sanitized_kwargs always present, even for empty kwargs
        assert resp["sanitized_kwargs"] == {}

    async def test_dry_run_dedupes_record_ids(
        self, execute_method_fn: tuple[Any, AsyncMock]
    ) -> None:
        execute_method, _client = execute_method_fn
        resp = await execute_method(
            model="sale.order",
            method="action_confirm",
            record_ids=[1, 1, 2, 1, 3],
            args=None,
            kwargs=None,
            dry_run=True,
        )
        # Dedupe preserves order: 1, 2, 3
        assert resp["record_ids"] == [1, 2, 3]


class TestRealModeUsesSanitizedKwargs:
    async def test_real_call_dispatches_with_stripped_context(
        self, execute_method_fn: tuple[Any, AsyncMock]
    ) -> None:
        execute_method, client = execute_method_fn
        await execute_method(
            model="sale.order",
            method="action_confirm",
            record_ids=[1],
            args=[],
            kwargs={"context": {"lang": "en_US", "tracking_disable": True}},
            dry_run=False,
        )
        # Real call — Odoo gets the filtered context only
        call = client.execute_kw.await_args
        args, kwargs = call.args, call.kwargs
        # signature: (model, method, args, kwargs)
        passed_kwargs = args[3] if len(args) >= 4 else kwargs.get("kwargs", {})
        assert passed_kwargs == {"context": {"lang": "en_US"}}
