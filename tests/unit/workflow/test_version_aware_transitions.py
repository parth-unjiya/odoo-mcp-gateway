"""Regression tests for version-aware workflow filtering (P2-3).

Before v0.2.2-final, the gateway advertised BOTH ``action_lock`` and
``action_done`` for sale.order — one fails on Odoo 17, the other on
Odoo 18+. Callers got a 50/50 chance of picking the right one.

The fix uses ``TransitionDef.min_version`` / ``max_version`` to filter
transitions against the detected Odoo major version at request time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.core.version.adapters import (
    V17Adapter,
    V18Adapter,
    V19Adapter,
)
from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
from odoo_mcp_gateway.tools.workflow import register_workflow_tools


def _stub_gateway(adapter: object | None = None) -> MagicMock:
    gw = MagicMock()
    gw.auth_managers = {}
    gw.restrictions.check_model_access = MagicMock(return_value=None)
    gw.restrictions.check_method_access = MagicMock(return_value=None)
    gw.sanitize_error = lambda exc: str(exc)
    gw.version_adapter = adapter
    gw.rate_limiter = None
    gw.rbac = None
    gw.audit_logger = None
    return gw


def _make_get_record_actions(gw: MagicMock) -> object:
    server = MagicMock()
    captured: dict[str, object] = {}

    def _capture_tool() -> object:
        def decorator(fn: object) -> object:
            captured[fn.__name__] = fn  # type: ignore[attr-defined]
            return fn

        return decorator

    server.tool = _capture_tool
    registry = WorkflowRegistry()
    registry.load_stock_workflows()
    register_workflow_tools(server, gw, registry)
    return captured["get_record_actions"]


def _stub_session(gw: MagicMock, state: str = "sale") -> None:
    client = AsyncMock()
    client.execute_kw = AsyncMock(return_value=[{"state": state}])
    mgr = MagicMock()
    mgr.get_active_client = MagicMock(return_value=client)
    mgr.auth_result = MagicMock(is_admin=False)
    gw.auth_managers = {"k": mgr}


@pytest.mark.parametrize(
    "adapter_cls,expected_lock_method",
    [
        (V17Adapter, "action_done"),
        (V18Adapter, "action_lock"),
        (V19Adapter, "action_lock"),
    ],
)
class TestSaleOrderLockMethodPerVersion:
    async def test_only_supported_lock_method_advertised(
        self, adapter_cls: type, expected_lock_method: str
    ) -> None:
        gw = _stub_gateway(adapter=adapter_cls())
        get_record_actions = _make_get_record_actions(gw)
        _stub_session(gw, state="sale")

        resp = await get_record_actions(model="sale.order", record_id=1)  # type: ignore[operator]

        # Collect the method names advertised for the 'done' transition.
        done_methods = [
            a["method"] for a in resp["actions"] if a.get("target_state") == "done"
        ]
        assert expected_lock_method in done_methods
        # The OTHER method must NOT appear (no version straddling).
        other = (
            "action_lock" if expected_lock_method == "action_done" else "action_done"
        )
        assert other not in done_methods


class TestPurchaseOrderV19Fallback:
    async def test_v19_advertises_write_locked_field(self) -> None:
        gw = _stub_gateway(adapter=V19Adapter())
        get_record_actions = _make_get_record_actions(gw)
        _stub_session(gw, state="purchase")

        resp = await get_record_actions(model="purchase.order", record_id=1)  # type: ignore[operator]
        # On Odoo 19 the canonical lock path is writing locked=True via
        # update_record. button_done was removed.
        done_actions = [a for a in resp["actions"] if a.get("target_state") == "done"]
        assert any(a.get("transition_via") == "update_record" for a in done_actions)
        assert any(a.get("write_field") == "locked" for a in done_actions)
        # button_done must NOT be advertised on v19
        assert not any(a.get("method") == "button_done" for a in done_actions)

    async def test_v17_advertises_button_done(self) -> None:
        gw = _stub_gateway(adapter=V17Adapter())
        get_record_actions = _make_get_record_actions(gw)
        _stub_session(gw, state="purchase")

        resp = await get_record_actions(model="purchase.order", record_id=1)  # type: ignore[operator]
        done_actions = [a for a in resp["actions"] if a.get("target_state") == "done"]
        assert any(a.get("method") == "button_done" for a in done_actions)
        # No write:locked alternative for v17
        assert not any(a.get("write_field") == "locked" for a in done_actions)


class TestUnknownVersionFailsOpen:
    async def test_no_adapter_advertises_all_versioned_transitions(self) -> None:
        gw = _stub_gateway(adapter=None)
        get_record_actions = _make_get_record_actions(gw)
        _stub_session(gw, state="sale")

        resp = await get_record_actions(model="sale.order", record_id=1)  # type: ignore[operator]
        done_methods = [
            a["method"] for a in resp["actions"] if a.get("target_state") == "done"
        ]
        # When version is unknown we must NOT silently filter out
        # version-constrained transitions — the caller can pick.
        assert "action_lock" in done_methods
        assert "action_done" in done_methods
