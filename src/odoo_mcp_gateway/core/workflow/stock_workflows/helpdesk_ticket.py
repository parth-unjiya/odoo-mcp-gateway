"""Workflow definition for helpdesk.ticket (Helpdesk Ticket)."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)


def get_workflow() -> WorkflowDef:
    """Return the helpdesk.ticket workflow definition.

    Helpdesk tickets use stage_id (a many2one to helpdesk.stage) rather
    than a fixed selection field. Stages are configurable per team, so
    the states defined here represent the typical default configuration.

    The workflow documents common actions rather than fixed transitions.
    """
    return WorkflowDef(
        model="helpdesk.ticket",
        display_name="Helpdesk Ticket",
        state_field="stage_id",
        states={
            "new": StateDef(
                name="new",
                label="New",
                transitions=(
                    TransitionDef(
                        action="assign_ticket",
                        target_state="in_progress",
                        label="Assign",
                        description=(
                            "Assign the ticket to a user. Set the "
                            "user_id field via update_record."
                        ),
                    ),
                ),
            ),
            "in_progress": StateDef(
                name="in_progress",
                label="In Progress",
                transitions=(
                    TransitionDef(
                        action="resolve_ticket",
                        target_state="solved",
                        label="Resolve",
                        description=(
                            "Mark the ticket as solved. Move to the "
                            "'Solved' stage via update_record on "
                            "stage_id."
                        ),
                    ),
                ),
            ),
            "solved": StateDef(
                name="solved",
                label="Solved",
                transitions=(
                    TransitionDef(
                        action="close_ticket",
                        target_state="closed",
                        label="Close",
                        description=(
                            "Close the resolved ticket. Move to the "
                            "'Closed' / 'Done' stage via update_record "
                            "on stage_id."
                        ),
                    ),
                    TransitionDef(
                        action="reopen_ticket",
                        target_state="in_progress",
                        label="Reopen",
                        description=(
                            "Reopen the ticket if the issue recurs. "
                            "Move back to the 'In Progress' stage."
                        ),
                    ),
                ),
            ),
            "closed": StateDef(
                name="closed",
                label="Closed",
                transitions=(),
            ),
        },
        create_guide=CreateGuide(
            required_relations=(
                RelationHint(
                    field_name="team_id",
                    relation_model="helpdesk.team",
                    hint=(
                        "Search helpdesk.team for the support team. "
                        "The team determines available stages and SLA "
                        "policies."
                    ),
                    required=True,
                ),
                RelationHint(
                    field_name="partner_id",
                    relation_model="res.partner",
                    hint=(
                        "Search res.partner for the customer who "
                        "reported the issue. Optional but recommended."
                    ),
                    required=False,
                ),
            ),
            recommended_fields=(
                "name",
                "description",
                "priority",
                "user_id",
                "tag_ids",
            ),
            notes=(
                "Stages are managed via stage_id (many2one to "
                "helpdesk.stage). The actual stages depend on the team "
                "configuration. Use search_read on helpdesk.stage with "
                "domain [['team_ids', 'in', [team_id]]] to discover "
                "available stages for a team. Transition between stages "
                "by updating stage_id via update_record."
            ),
        ),
        version_notes={},
    )
