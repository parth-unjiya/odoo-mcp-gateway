"""Workflow-aware tools that guide AI agents through Odoo business processes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
from odoo_mcp_gateway.server import _get_auth_manager, _get_client
from odoo_mcp_gateway.tools.crud import _validate_model

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


def register_workflow_tools(
    server: FastMCP,
    gateway: GatewayContext,
    workflow_registry: WorkflowRegistry,
) -> None:
    """Register workflow guidance tools on the server."""

    @server.tool()
    async def get_create_requirements(model: str) -> dict[str, Any]:
        """Get everything needed to create a record of any Odoo model.

        Returns required fields (from live schema), recommended fields,
        relation hints (which related records to look up first), and
        creation notes from the workflow engine.

        This is the first tool to call before create_record.
        """
        try:
            model = _validate_model(model)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(
                gateway, "get_create_requirements", session_key
            )
            if gate_error:
                return {"error": gate_error}

            # Check model restrictions for create
            restriction_msg = gateway.restrictions.check_model_access(
                model, "create", is_admin
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Fetch live field definitions
            fields = await gateway.field_inspector.get_fields(client, model)

            # Apply RBAC field filtering
            redact_fields = gateway.rbac.get_visible_fields(
                model, user_groups, is_admin
            )

            # Categorise fields
            required_fields: dict[str, Any] = {}
            optional_fields: dict[str, Any] = {}

            for fname, finfo in fields.items():
                # Skip redacted fields
                if redact_fields is not None and fname in redact_fields:
                    continue
                # Skip readonly and computed fields
                if finfo.readonly:
                    continue
                # Skip binary fields
                if finfo.is_binary:
                    continue

                entry: dict[str, Any] = {
                    "type": finfo.field_type,
                    "string": finfo.string,
                    "relation": finfo.relation,
                }
                if finfo.selection:
                    entry["selection"] = finfo.selection
                if finfo.help_text:
                    entry["help"] = finfo.help_text

                if finfo.required:
                    required_fields[fname] = entry
                else:
                    optional_fields[fname] = entry

            # Build result
            result: dict[str, Any] = {
                "model": model,
                "required_fields": required_fields,
                "required_field_count": len(required_fields),
                "optional_field_count": len(optional_fields),
            }

            # Enrich with workflow create guide if available
            wf = workflow_registry.get(model)
            if wf is not None and wf.create_guide is not None:
                guide = wf.create_guide
                result["relation_hints"] = [
                    {
                        "field_name": r.field_name,
                        "relation_model": r.relation_model,
                        "hint": r.hint,
                        "required": r.required,
                    }
                    for r in guide.required_relations
                ]
                result["recommended_fields"] = list(guide.recommended_fields)
                if guide.line_model:
                    result["line_model"] = guide.line_model
                    result["line_field"] = guide.line_field
                if guide.notes:
                    result["notes"] = guide.notes

            if wf is not None and wf.version_notes:
                result["version_notes"] = dict(wf.version_notes)

            return result

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_create_requirements")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def get_record_actions(
        model: str,
        record_id: int,
    ) -> dict[str, Any]:
        """Get the current state and available next actions for a record.

        Returns the current state label and a list of valid transitions
        (filtered by security restrictions), each with method name,
        label, description, and target state.

        This is the tool to call after reading a record to know what
        workflow actions are available.
        """
        try:
            model = _validate_model(model)
            if record_id <= 0:
                return {"error": "record_id must be a positive integer"}

            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(
                gateway, "get_record_actions", session_key
            )
            if gate_error:
                return {"error": gate_error}

            # Check model restrictions for read
            restriction_msg = gateway.restrictions.check_model_access(
                model, "read", is_admin
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Look up workflow
            wf = workflow_registry.get(model)
            if wf is None:
                return {
                    "model": model,
                    "record_id": record_id,
                    "has_workflow": False,
                    "message": (
                        f"No workflow definition available for '{model}'. "
                        "Use get_model_fields to inspect the state field "
                        "manually."
                    ),
                }

            # Read the current state value from the record
            records = await client.execute_kw(
                model,
                "read",
                [[record_id]],
                {"fields": [wf.state_field]},
            )

            if not records:
                return {"error": f"Record {record_id} not found in {model}"}

            record = records[0] if isinstance(records, list) else records
            raw_state = record.get(wf.state_field)

            # For stage-based workflows (many2one), extract the ID
            if isinstance(raw_state, list) and len(raw_state) >= 1:
                # Many2one returns [id, display_name]
                current_state_display = (
                    raw_state[1] if len(raw_state) > 1 else str(raw_state[0])
                )
                # Stage-based: we cannot map to our static states directly,
                # so return all known actions
                return _build_stage_based_response(
                    wf, model, record_id, raw_state, current_state_display,
                    is_admin,
                )

            # Selection-based state field
            current_state = str(raw_state) if raw_state else ""
            state_def = wf.states.get(current_state)

            if state_def is None:
                return {
                    "model": model,
                    "record_id": record_id,
                    "has_workflow": True,
                    "state_field": wf.state_field,
                    "current_state": current_state,
                    "current_state_label": current_state,
                    "actions": [],
                    "message": (
                        f"State '{current_state}' is not in the known "
                        f"workflow definition for '{model}'."
                    ),
                }

            # Build available actions, filtering by restrictions
            actions = _filter_transitions(
                wf, state_def.transitions, model, is_admin
            )

            return {
                "model": model,
                "record_id": record_id,
                "has_workflow": True,
                "state_field": wf.state_field,
                "current_state": current_state,
                "current_state_label": state_def.label,
                "actions": actions,
            }

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_record_actions")
            return {"error": gateway.sanitize_error(e)}

    def _build_stage_based_response(
        wf: Any,
        model: str,
        record_id: int,
        raw_state: Any,
        display_name: str,
        is_admin: bool,
    ) -> dict[str, Any]:
        """Build response for stage-based (many2one) workflows."""
        # Collect all unique actions from all states
        all_transitions: list[Any] = []
        seen_actions: set[str] = set()
        for state_def in wf.states.values():
            for t in state_def.transitions:
                if t.action not in seen_actions:
                    seen_actions.add(t.action)
                    all_transitions.append(t)

        actions = _filter_transitions(
            wf, tuple(all_transitions), model, is_admin
        )

        return {
            "model": model,
            "record_id": record_id,
            "has_workflow": True,
            "state_field": wf.state_field,
            "current_state": raw_state,
            "current_state_label": display_name,
            "stage_based": True,
            "actions": actions,
            "message": (
                f"'{model}' uses stage-based workflow (many2one). "
                "Stages are configurable per team. The listed actions "
                "are common operations; actual stage transitions may "
                "also be done via update_record on stage_id."
            ),
        }

    def _filter_transitions(
        wf: Any,
        transitions: tuple[Any, ...],
        model: str,
        is_admin: bool,
    ) -> list[dict[str, Any]]:
        """Filter transitions by restriction checker, return as dicts."""
        actions: list[dict[str, Any]] = []
        for t in transitions:
            # Check if the method is allowed
            method_msg = gateway.restrictions.check_method_access(
                model, t.action, is_admin
            )
            if method_msg:
                continue

            actions.append({
                "method": t.action,
                "label": t.label,
                "description": t.description,
                "target_state": t.target_state,
            })

        return actions
