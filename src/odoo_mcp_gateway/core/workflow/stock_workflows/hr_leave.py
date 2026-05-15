"""Workflow definition for hr.leave (Time Off / Leave Request)."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)


def get_workflow() -> WorkflowDef:
    """Return the hr.leave workflow definition.

    State machine:
    draft -> confirm -> validate1 -> validate / refuse
    """
    return WorkflowDef(
        model="hr.leave",
        display_name="Time Off Request",
        state_field="state",
        states={
            "draft": StateDef(
                name="draft",
                label="To Submit",
                transitions=(
                    TransitionDef(
                        action="action_confirm",
                        target_state="confirm",
                        label="Confirm",
                        description=("Submits the leave request for manager approval."),
                    ),
                ),
            ),
            "confirm": StateDef(
                name="confirm",
                label="To Approve",
                transitions=(
                    TransitionDef(
                        action="action_approve",
                        target_state="validate",
                        label="Approve",
                        description=(
                            "Approves the leave request. If the leave "
                            "type requires double validation, this moves "
                            "to 'Second Approval' instead."
                        ),
                    ),
                    TransitionDef(
                        action="action_refuse",
                        target_state="refuse",
                        label="Refuse",
                        description="Refuses the leave request.",
                    ),
                    TransitionDef(
                        action="action_draft",
                        target_state="draft",
                        label="Reset to Draft",
                        description=("Resets the request back to draft for editing."),
                    ),
                ),
            ),
            "validate1": StateDef(
                name="validate1",
                label="Second Approval",
                transitions=(
                    TransitionDef(
                        action="action_validate",
                        target_state="validate",
                        label="Validate",
                        description=(
                            "Final approval for leave types that "
                            "require double validation."
                        ),
                    ),
                    TransitionDef(
                        action="action_refuse",
                        target_state="refuse",
                        label="Refuse",
                        description="Refuses the leave request.",
                    ),
                ),
            ),
            "validate": StateDef(
                name="validate",
                label="Approved",
                transitions=(
                    TransitionDef(
                        action="action_refuse",
                        target_state="refuse",
                        label="Refuse",
                        description=(
                            "Revokes the approved leave. Use with "
                            "caution as this may affect payroll."
                        ),
                    ),
                ),
            ),
            "refuse": StateDef(
                name="refuse",
                label="Refused",
                transitions=(
                    TransitionDef(
                        action="action_draft",
                        target_state="draft",
                        label="Reset to Draft",
                        description=(
                            "Resets a refused request back to draft for re-submission."
                        ),
                    ),
                ),
            ),
        },
        create_guide=CreateGuide(
            required_relations=(
                RelationHint(
                    field_name="holiday_status_id",
                    relation_model="hr.leave.type",
                    hint=(
                        "Search hr.leave.type for the leave type. "
                        "Use search_read to find available leave types "
                        "like 'Paid Time Off' or 'Sick Leave'."
                    ),
                    required=True,
                ),
                RelationHint(
                    field_name="employee_id",
                    relation_model="hr.employee",
                    hint=(
                        "Search hr.employee for the employee. "
                        "If creating for yourself, this defaults to "
                        "the current user's employee record."
                    ),
                    required=True,
                ),
            ),
            recommended_fields=(
                "date_from",
                "date_to",
                "name",
                "number_of_days",
            ),
            notes=(
                "Provide date_from and date_to as datetime strings "
                "(YYYY-MM-DD HH:MM:SS). The number_of_days is auto-"
                "computed. After creation, call action_confirm to "
                "submit for approval."
            ),
        ),
        version_notes={},
    )
