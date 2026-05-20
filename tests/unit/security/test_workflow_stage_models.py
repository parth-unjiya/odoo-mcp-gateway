"""UAT MED-1 (Odoo 19) — stage / team models must be discoverable.

``update_task_stage`` and ``update_ticket_stage`` both require a
``stage_id`` argument. Without ``project.task.type`` and
``helpdesk.stage`` (plus ``helpdesk.team`` for stage→team scoping) in
the model registry, callers had no way to enumerate valid stage IDs
through the gateway. We expose them as read-only in the example YAML:
they are operator-managed structural records, never written via MCP.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.core.security.restrictions import RestrictionChecker

_EXAMPLE = (
    Path(__file__).parent.parent.parent.parent / "config" / "model_access.yaml.example"
)


def _checker_from_example() -> RestrictionChecker:
    data = yaml.safe_load(_EXAMPLE.read_text())
    model_access = ModelAccessConfig(**data)
    restrictions = RestrictionConfig()
    return RestrictionChecker(restrictions, model_access)


class TestWorkflowStageModelsReadable:
    """All three stage/team models are READ-accessible to non-admin users."""

    def test_project_task_type_readable_non_admin(self) -> None:
        checker = _checker_from_example()
        assert checker.check_model_access("project.task.type", "read", False) is None

    def test_helpdesk_stage_readable_non_admin(self) -> None:
        checker = _checker_from_example()
        assert checker.check_model_access("helpdesk.stage", "read", False) is None

    def test_helpdesk_team_readable_non_admin(self) -> None:
        checker = _checker_from_example()
        assert checker.check_model_access("helpdesk.team", "read", False) is None


class TestWorkflowStageModelsReadOnly:
    """Stage/team models are NOT writable — operator concern only."""

    def test_project_task_type_write_blocked(self) -> None:
        checker = _checker_from_example()
        msg = checker.check_model_access("project.task.type", "write", False)
        assert msg is not None
        assert "read-only" in msg

    def test_helpdesk_stage_create_blocked(self) -> None:
        checker = _checker_from_example()
        msg = checker.check_model_access("helpdesk.stage", "create", False)
        assert msg is not None

    def test_helpdesk_team_delete_blocked_even_for_admin(self) -> None:
        """Read-only models stay read-only even for admin."""
        checker = _checker_from_example()
        msg = checker.check_model_access("helpdesk.team", "delete", True)
        assert msg is not None
