"""Workflow registry: stores and retrieves workflow definitions."""

from __future__ import annotations

import logging
from typing import Any

from odoo_mcp_gateway.core.workflow.definitions import WorkflowDef

logger = logging.getLogger(__name__)


class WorkflowRegistry:
    """Registry of workflow definitions for Odoo models.

    Provides lookup, listing, and serialisation of workflow state machines.
    """

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDef] = {}

    def register(self, workflow: WorkflowDef) -> None:
        """Register a workflow definition.

        If a workflow for the same model already exists it is replaced.
        """
        self._workflows[workflow.model] = workflow
        logger.debug("Registered workflow for %s", workflow.model)

    def get(self, model: str) -> WorkflowDef | None:
        """Return the workflow definition for *model*, or ``None``."""
        return self._workflows.get(model)

    def list_models(self) -> list[str]:
        """Return a sorted list of models that have workflow definitions."""
        return sorted(self._workflows.keys())

    def load_stock_workflows(self) -> None:
        """Load all built-in stock workflow definitions."""
        from odoo_mcp_gateway.core.workflow.stock_workflows import (
            get_all_stock_workflows,
        )

        for wf in get_all_stock_workflows():
            self.register(wf)
        logger.info(
            "Loaded %d stock workflows: %s",
            len(self._workflows),
            ", ".join(sorted(self._workflows.keys())),
        )

    def to_dict(self, model: str) -> dict[str, Any] | None:
        """Convert a workflow definition to a JSON-serialisable dict.

        Returns ``None`` if no workflow is registered for *model*.
        """
        wf = self._workflows.get(model)
        if wf is None:
            return None

        states: dict[str, Any] = {}
        for state_val, state_def in wf.states.items():
            transitions = [
                {
                    "action": t.action,
                    "target_state": t.target_state,
                    "label": t.label,
                    "description": t.description,
                }
                for t in state_def.transitions
            ]
            states[state_val] = {
                "label": state_def.label,
                "transitions": transitions,
            }

        result: dict[str, Any] = {
            "model": wf.model,
            "display_name": wf.display_name,
            "state_field": wf.state_field,
            "states": states,
        }

        if wf.create_guide is not None:
            guide = wf.create_guide
            result["create_guide"] = {
                "required_relations": [
                    {
                        "field_name": r.field_name,
                        "relation_model": r.relation_model,
                        "hint": r.hint,
                        "required": r.required,
                    }
                    for r in guide.required_relations
                ],
                "recommended_fields": list(guide.recommended_fields),
                "line_model": guide.line_model,
                "line_field": guide.line_field,
                "notes": guide.notes,
            }

        if wf.version_notes:
            result["version_notes"] = dict(wf.version_notes)

        return result
