"""Workflow-aware tools that guide AI agents through Odoo business processes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
from odoo_mcp_gateway.server import (
    _get_auth_manager,
    _get_client,
    get_current_session_key,
)
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

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
            gate_error = await security_gate(gateway, "get_record_actions", session_key)
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
                    wf,
                    model,
                    record_id,
                    raw_state,
                    current_state_display,
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
            actions = _filter_transitions(wf, state_def.transitions, model, is_admin)

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
        """Build response for stage-based (many2one) workflows.

        Stage-based workflows (helpdesk.ticket, project.task, crm.lead)
        usually express every transition as ``write:stage_id`` — the
        action string is identical across transitions. Two transitions
        can also share the same target_state (e.g. ``new → in_progress``
        AND ``solved → in_progress`` are both "move to In Progress"
        but the second is semantically *reopening*). The dedupe key
        therefore includes the human-facing label so user-distinct
        transitions all survive — ``(action, target_state, label)``.
        """
        all_transitions: list[Any] = []
        seen: set[tuple[str, str, str]] = set()
        for state_def in wf.states.values():
            for t in state_def.transitions:
                key = (str(t.action), str(t.target_state), str(t.label))
                if key not in seen:
                    seen.add(key)
                    all_transitions.append(t)

        actions = _filter_transitions(wf, tuple(all_transitions), model, is_admin)

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
        """Filter transitions and return as dicts annotated by transition type.

        Two kinds of transitions are supported:

        * Standard method-call transitions (e.g. ``action_confirm``): the
          ``action`` string is the Odoo method name. Filtered through
          ``restrictions.check_method_access``. The response includes
          ``"transition_via": "execute_method"``.
        * Field-write transitions (action starts with ``write:``): the
          transition is performed by writing the named field via
          ``update_record``. The response surfaces ``"transition_via":
          "update_record"`` and a ``"write_field"`` hint so the AI
          client can construct the correct call. ``check_method_access``
          is skipped — there's no method to authorize, just a field write.

        Transitions with version constraints (``min_version`` /
        ``max_version``) are filtered against the currently detected
        Odoo major version so callers never see, e.g., ``action_done``
        on Odoo 19 or ``action_lock`` on Odoo 17.
        """
        adapter = getattr(gateway, "version_adapter", None)
        current_major: int | None = (
            getattr(adapter, "major_version", None) if adapter is not None else None
        )
        # major_version=0 (the abstract default) is treated as "unknown"
        # so a misconfigured adapter doesn't silently filter everything.
        if current_major == 0:
            current_major = None

        actions: list[dict[str, Any]] = []
        for t in transitions:
            # Skip transitions that don't apply to this Odoo version.
            is_supported = getattr(t, "is_supported_on", None)
            if callable(is_supported) and not is_supported(current_major):
                continue

            # Field-write transitions don't have a corresponding Odoo
            # method — they are documentation that the AI should use
            # update_record on the named field.
            if isinstance(t.action, str) and t.action.startswith("write:"):
                write_field = t.action.split(":", 1)[1] or wf.state_field
                actions.append(
                    {
                        "method": None,
                        "transition_via": "update_record",
                        "write_field": write_field,
                        "label": t.label,
                        "description": t.description,
                        "target_state": t.target_state,
                    }
                )
                continue

            # Standard method-call transition — apply restriction checker
            method_msg = gateway.restrictions.check_method_access(
                model, t.action, is_admin
            )
            if method_msg:
                continue

            actions.append(
                {
                    "method": t.action,
                    "transition_via": "execute_method",
                    "label": t.label,
                    "description": t.description,
                    "target_state": t.target_state,
                }
            )

        return actions

    # Register operation types for security middleware
    from odoo_mcp_gateway.core.security import register_tool_operations

    register_tool_operations(
        {
            "get_create_requirements": "read",
            "get_record_actions": "read",
        }
    )
