"""Centralised MCP tool annotations for every tool the gateway exposes.

Tool annotations are MCP-spec-level hints (`readOnlyHint`,
`destructiveHint`, `idempotentHint`, `openWorldHint`) that clients use
to drive UX — e.g. "ask the user before running destructive tools" or
"safe to re-try idempotent ones automatically." See ADR-007 in
``.release-drafts/v030-plan.md`` for the full rationale.

Annotations are HINTS only — they don't change runtime enforcement.
The gateway's existing restrictions/RBAC/audit layers remain the
binding security controls. Annotations help the client + the LLM
understand what each tool DOES so the UX flow is correct.

The four flags (per the MCP spec):

* ``readOnlyHint=True`` — the tool does not modify Odoo state.
* ``destructiveHint=True`` — the tool may delete data or otherwise
  apply destructive updates. The MCP spec defaults this to ``True``
  when ``readOnlyHint`` is unset/false, so we mark it explicitly only
  where we want to ANNOUNCE that the tool's writes are non-destructive
  (just additive).
* ``idempotentHint=True`` — repeating the call with the same arguments
  has no additional effect. In Odoo this is true for ``write`` by id
  (overwriting field values with the same payload is a no-op for the
  database), and for state-machine "move to stage X" calls when the
  record is already in stage X.
* ``openWorldHint=False`` — the tool's domain of interaction is the
  single configured Odoo instance, not the open internet. ALL our
  tools are closed-world by definition.

This file is the single source of truth. ``apply_annotations`` is the
helper every tool registration calls so the surface is uniform.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

# ---------------------------------------------------------------------
# Per-tool annotation map.
#
# Maintenance rules:
# * Add the tool here when you add a @server.tool() — the registration
#   helper looks up annotations by tool name.
# * Read-only tools: set ``readOnlyHint=True``. The other flags are
#   meaningless when read-only is true (per MCP spec).
# * Write tools: set ``readOnlyHint=False``. Decide destructiveHint /
#   idempotentHint based on semantics.
# * Every tool sets ``openWorldHint=False`` — we only talk to Odoo.
# ---------------------------------------------------------------------

_TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    # ── auth ──────────────────────────────────────────────────────
    "login": ToolAnnotations(
        title="Authenticate with Odoo",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,  # re-login with same creds is a no-op outcome
        openWorldHint=False,
    ),
    # ── schema (read-only) ───────────────────────────────────────
    "list_models": ToolAnnotations(
        title="List Odoo models",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_model_fields": ToolAnnotations(
        title="Inspect fields of an Odoo model",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    # ── CRUD reads ────────────────────────────────────────────────
    "search_read": ToolAnnotations(
        title="Search and read records",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_record": ToolAnnotations(
        title="Fetch a record by ID",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "search_count": ToolAnnotations(
        title="Count records matching a domain",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "read_group": ToolAnnotations(
        title="Group records and aggregate",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_defaults": ToolAnnotations(
        title="Get default field values for a model",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_onchange": ToolAnnotations(
        title="Simulate an onchange event",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    # ── CRUD writes ───────────────────────────────────────────────
    "create_record": ToolAnnotations(
        title="Create a new Odoo record",
        readOnlyHint=False,
        destructiveHint=False,  # additive only
        idempotentHint=False,  # each call makes a new record
        openWorldHint=False,
    ),
    "update_record": ToolAnnotations(
        title="Update an existing Odoo record",
        readOnlyHint=False,
        destructiveHint=False,  # overwrites named fields; doesn't delete
        idempotentHint=True,  # write-by-id with same vals is a no-op
        openWorldHint=False,
    ),
    "delete_record": ToolAnnotations(
        title="Delete an Odoo record",
        readOnlyHint=False,
        destructiveHint=True,  # the canonical destructive op
        idempotentHint=True,  # deleting a deleted record errors but is a no-op
        openWorldHint=False,
    ),
    "execute_method": ToolAnnotations(
        title="Call an Odoo model method",
        readOnlyHint=False,
        # Workflow methods can have side effects (e.g. action_confirm
        # creates stock pickings); err on the cautious side.
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
    # ── workflow ──────────────────────────────────────────────────
    "get_create_requirements": ToolAnnotations(
        title="Get the schema needed to create a record",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_record_actions": ToolAnnotations(
        title="List available workflow actions for a record",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    # ── plugin: HR ────────────────────────────────────────────────
    "get_my_profile": ToolAnnotations(
        title="Get the current user's HR profile",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_my_attendance": ToolAnnotations(
        title="List the current user's attendance records",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "check_in": ToolAnnotations(
        title="Clock in attendance",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,  # each call creates a new attendance
        openWorldHint=False,
    ),
    "check_out": ToolAnnotations(
        title="Clock out attendance",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,  # closing an already-closed record is a no-op
        openWorldHint=False,
    ),
    "get_my_leaves": ToolAnnotations(
        title="List the current user's leave requests",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "request_leave": ToolAnnotations(
        title="Submit a leave request",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,  # each call creates a new leave
        openWorldHint=False,
    ),
    # ── plugin: Sales ─────────────────────────────────────────────
    "get_my_quotations": ToolAnnotations(
        title="List the current user's quotations",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_order_details": ToolAnnotations(
        title="Fetch full details for a sales order",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "confirm_order": ToolAnnotations(
        title="Confirm a quotation into a sales order",
        readOnlyHint=False,
        destructiveHint=False,  # additive workflow transition
        idempotentHint=True,  # confirming an already-confirmed order = no-op
        openWorldHint=False,
    ),
    "get_sales_summary": ToolAnnotations(
        title="Aggregate sales totals over a period",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    # ── plugin: Project ───────────────────────────────────────────
    "get_my_tasks": ToolAnnotations(
        title="List tasks assigned to the current user",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "get_project_summary": ToolAnnotations(
        title="Get summary stats for a project",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "update_task_stage": ToolAnnotations(
        title="Move a project task to a new stage",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,  # setting stage to the current stage = no-op
        openWorldHint=False,
    ),
    # ── plugin: Helpdesk ─────────────────────────────────────────
    "get_my_tickets": ToolAnnotations(
        title="List helpdesk tickets owned by the current user",
        readOnlyHint=True,
        openWorldHint=False,
    ),
    "create_ticket": ToolAnnotations(
        title="Create a new helpdesk ticket",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,  # each call creates a new ticket
        openWorldHint=False,
    ),
    "update_ticket_stage": ToolAnnotations(
        title="Move a helpdesk ticket to a new stage",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
}


def get_annotations(tool_name: str) -> ToolAnnotations | None:
    """Look up the central annotation map for *tool_name*.

    Returns ``None`` for tools that haven't been registered in this
    file (callers should still register without annotations rather
    than crashing). The Sprint 2 verification test asserts every
    tool the server exposes has an entry here, so a missing
    annotation surfaces immediately in CI.
    """
    return _TOOL_ANNOTATIONS.get(tool_name)


def get_all_annotated_tool_names() -> frozenset[str]:
    """Return the set of tool names known to this annotation map.

    Used by the Sprint 2 conformance test to detect drift between
    the annotation map and the tools actually registered on the server.
    """
    return frozenset(_TOOL_ANNOTATIONS)


def apply_pending_annotations(server: object) -> dict[str, str]:
    """Attach annotations from the central map to every registered tool.

    Called ONCE after all ``register_*_tools`` and plugin activation
    completes. Walks ``server._tool_manager._tools`` and, for each
    tool whose name appears in ``_TOOL_ANNOTATIONS``, sets the
    Pydantic ``annotations`` field on the Tool record.

    Returns a diagnostic dict mapping ``tool_name → status``:
    * ``"annotated"`` — annotation found and attached.
    * ``"missing_from_map"`` — tool registered but has no entry here.
    * ``"already_annotated"`` — tool came pre-annotated (we don't
      overwrite; the explicit-at-call-site annotation wins).

    The Sprint 2 conformance test treats ``missing_from_map`` as a
    failure so new tools cannot accidentally ship without annotations.
    """
    # Late import to avoid a circular: this module is imported by
    # tools/* which are imported via server.create_server, which uses
    # this function at the end of its body.
    tool_manager = getattr(server, "_tool_manager", None)
    if tool_manager is None:
        return {}
    tools: dict[str, object] = getattr(tool_manager, "_tools", {})

    report: dict[str, str] = {}
    for name, tool in tools.items():
        existing = getattr(tool, "annotations", None)
        if existing is not None:
            report[name] = "already_annotated"
            continue
        annotations = _TOOL_ANNOTATIONS.get(name)
        if annotations is None:
            report[name] = "missing_from_map"
            continue
        # Tool is a Pydantic model with ``annotations`` field — direct
        # attribute set works because Pydantic v2 models allow runtime
        # field assignment when ``model_config`` does NOT freeze.
        try:
            tool.annotations = annotations  # type: ignore[attr-defined]
            report[name] = "annotated"
        except Exception:
            # If FastMCP ever makes Tool frozen, we'll need a different
            # plumbing path (likely registering with annotations at
            # call time). Surface the failure so CI catches it.
            report[name] = "set_failed"
    return report
