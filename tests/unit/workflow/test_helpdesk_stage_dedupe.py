"""Regression test for helpdesk stage transition dedupe (H2).

Before v0.2.2-final, ``_build_stage_based_response`` deduped transitions
by ``t.action`` alone. Every helpdesk transition uses
``action="write:stage_id"``, so the first transition encountered was
kept and the other three (in_progress→solved, solved→closed,
solved→in_progress) were silently dropped.

The fix changes the dedupe key to ``(action, target_state)`` so each
distinct stage move survives.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
from odoo_mcp_gateway.tools.workflow import register_workflow_tools


def _stub_gateway() -> MagicMock:
    gw = MagicMock()
    gw.auth_managers = {}
    gw.restrictions.check_model_access = MagicMock(return_value=None)
    gw.restrictions.check_method_access = MagicMock(return_value=None)
    gw.sanitize_error = lambda exc: str(exc)
    gw.version_adapter = None
    # security_gate calls these — set to None so the gate runs in pass-through
    # mode rather than trying to unpack a MagicMock-returned tuple.
    gw.rate_limiter = None
    gw.rbac = None
    gw.audit_logger = None
    return gw


@pytest.fixture
def workflow_server() -> tuple[MagicMock, MagicMock, WorkflowRegistry]:
    gw = _stub_gateway()
    server = MagicMock()
    registered: dict[str, object] = {}

    def _capture_tool() -> object:
        def decorator(fn: object) -> object:
            registered[fn.__name__] = fn  # type: ignore[attr-defined]
            return fn

        return decorator

    server.tool = _capture_tool
    registry = WorkflowRegistry()
    registry.load_stock_workflows()
    register_workflow_tools(server, gw, registry)
    return gw, registered["get_record_actions"], registry  # type: ignore[return-value]


class TestHelpdeskStageDedupe:
    async def test_all_four_transitions_advertised(
        self, workflow_server: tuple[MagicMock, object, WorkflowRegistry]
    ) -> None:
        gw, get_record_actions, _registry = workflow_server

        # Mock the client so read() returns a record sitting in the
        # 'New' stage. The stage_id is many2one → returned as [id, name].
        client = AsyncMock()
        client.execute_kw = AsyncMock(return_value=[{"stage_id": [3, "New"]}])
        mgr = MagicMock()
        mgr.get_active_client = MagicMock(return_value=client)
        mgr.auth_result = MagicMock(is_admin=False)
        gw.auth_managers = {"k": mgr}

        resp = await get_record_actions(model="helpdesk.ticket", record_id=42)  # type: ignore[operator]

        assert resp.get("has_workflow") is True
        assert resp.get("stage_based") is True
        actions = resp["actions"]
        # All four distinct stage moves must be present:
        #   new → in_progress, in_progress → solved, solved → closed,
        #   solved → in_progress (reopen)
        targets = sorted(a["target_state"] for a in actions)
        assert targets == sorted(["in_progress", "solved", "closed", "in_progress"])
        # Each action is a stage-write annotation
        for a in actions:
            assert a["transition_via"] == "update_record"
            assert a["write_field"] == "stage_id"
