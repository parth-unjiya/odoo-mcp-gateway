"""Regression tests for get_onchange parity (M-tools-12).

Pre-fix gaps:
* ``get_onchange`` did NOT call ``_apply_value_renames`` → callers
  passing v18 field names (``tax_id``) on v19 hit the deprecated
  column directly.
* ``get_onchange`` did NOT call ``check_field_write`` on the values
  dict → field-existence probing for sensitive fields was possible
  (recon vector).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.tools.crud import register_crud_tools


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
def onchange_fn() -> Any:
    gw = MagicMock()
    client = AsyncMock()
    client.execute_kw = AsyncMock(return_value={"value": {}})
    mgr = MagicMock()
    mgr.get_active_client = MagicMock(return_value=client)
    mgr.auth_result = MagicMock(is_admin=False, groups=[])
    gw.auth_managers = {"k": mgr}
    gw.restrictions.check_model_access = MagicMock(return_value=None)
    gw.restrictions.check_field_write = MagicMock(return_value=None)
    gw.rbac = MagicMock()
    gw.rbac.check_tool_access = MagicMock(return_value=None)
    gw.rbac.filter_response_fields = MagicMock(
        side_effect=lambda recs, *_a, **_kw: recs
    )
    gw.field_inspector.get_fields = AsyncMock(return_value={})
    gw.version_adapter = None
    gw.sanitize_error = lambda exc: str(exc)
    # security_gate skips checks when these are None
    gw.rate_limiter = None
    gw.audit_logger = None
    server = MagicMock()
    captured = _capture_tools(server)
    register_crud_tools(server, gw)
    return captured["get_onchange"], gw, client


class TestOnchangeFieldWriteRestrictions:
    async def test_blocked_field_in_values_rejected(
        self, onchange_fn: tuple[Any, MagicMock, AsyncMock]
    ) -> None:
        get_onchange, gw, _client = onchange_fn
        # Pretend the gateway blocks 'password' field-writes.
        gw.restrictions.check_field_write.side_effect = lambda model, field, is_admin: (
            "blocked" if field == "password" else None
        )
        resp = await get_onchange(
            model="res.users",
            values={"password": "x", "name": "y"},
            changed_field="name",
        )
        assert "error" in resp
        assert resp["error"] == "blocked"

    async def test_normal_field_allowed(
        self, onchange_fn: tuple[Any, MagicMock, AsyncMock]
    ) -> None:
        get_onchange, _gw, client = onchange_fn
        resp = await get_onchange(
            model="sale.order",
            values={"partner_id": 1},
            changed_field="partner_id",
        )
        assert "error" not in resp
        # The onchange RPC was reached
        client.execute_kw.assert_awaited_once()


class TestOnchangeVersionRenames:
    async def test_v19_renames_applied_to_values(
        self, onchange_fn: tuple[Any, MagicMock, AsyncMock]
    ) -> None:
        get_onchange, gw, client = onchange_fn

        # Stub a v19 adapter that renames tax_id → tax_ids.
        class _Adapter:
            major_version = 19

            def get_renamed_fields(self, model: str) -> dict[str, str]:
                if model == "sale.order.line":
                    return {"tax_id": "tax_ids"}
                return {}

            def get_removed_fields(self, model: str) -> frozenset[str]:
                return frozenset()

        gw.version_adapter = _Adapter()

        await get_onchange(
            model="sale.order.line",
            values={"tax_id": [(6, 0, [1])]},
            changed_field="tax_id",
        )

        # The onchange call MUST have received the renamed field key.
        call = client.execute_kw.await_args
        positional = call.args
        # signature: (model, method, [args]) where args = [ids, values, changed, spec]
        passed_values = positional[2][1]
        passed_changed = positional[2][2]
        assert "tax_id" not in passed_values
        assert "tax_ids" in passed_values
        assert passed_changed == ["tax_ids"]
