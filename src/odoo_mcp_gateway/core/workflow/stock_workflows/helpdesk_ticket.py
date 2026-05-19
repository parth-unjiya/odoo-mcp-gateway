"""Workflow definition for helpdesk.ticket (Helpdesk Ticket).

Unlike ``sale.order`` / ``purchase.order`` which expose dedicated server
methods (``action_confirm``, ``button_validate``…), the ``helpdesk.ticket``
state machine is driven entirely by writing ``stage_id`` (a many2one to
``helpdesk.stage``). There are no Odoo-provided action methods named
``assign_ticket`` / ``resolve_ticket`` / ``close_ticket`` / ``reopen_ticket``
— attempting to call them via ``execute_method`` raises
``method does not exist``.

This workflow definition therefore models transitions as **field-write
documentation** rather than method calls. Each ``TransitionDef.action``
uses the special prefix ``write:`` to signal to ``get_record_actions`` /
``execute_method`` that the transition is performed via ``update_record``
on the ``stage_id`` field (which the gateway already supports).
"""

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
    than a fixed selection field. Stages are configurable per team.
    Transitions are performed by writing ``stage_id`` via
    ``update_record`` — the ``action`` strings below are documentation
    only (the ``write:`` prefix denotes this; they are not method names
    on the server).
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
                        action="write:stage_id",
                        target_state="in_progress",
                        label="Move to 'In Progress'",
                        description=(
                            "Move the ticket to the 'In Progress' stage. "
                            "Perform via `update_record(model='helpdesk.ticket', "
                            "record_id=<id>, values={'stage_id': <stage_id>})`. "
                            "Optionally set user_id at the same time to "
                            "assign an owner."
                        ),
                    ),
                ),
            ),
            "in_progress": StateDef(
                name="in_progress",
                label="In Progress",
                transitions=(
                    TransitionDef(
                        action="write:stage_id",
                        target_state="solved",
                        label="Move to 'Solved'",
                        description=(
                            "Mark the ticket as solved by moving to the "
                            "'Solved' stage. Perform via "
                            "`update_record(model='helpdesk.ticket', "
                            "record_id=<id>, values={'stage_id': "
                            "<solved_stage_id>})`."
                        ),
                    ),
                ),
            ),
            "solved": StateDef(
                name="solved",
                label="Solved",
                transitions=(
                    TransitionDef(
                        action="write:stage_id",
                        target_state="closed",
                        label="Move to 'Closed' / 'Done'",
                        description=(
                            "Close the resolved ticket by moving to the "
                            "'Closed' / 'Done' stage via update_record on "
                            "stage_id."
                        ),
                    ),
                    TransitionDef(
                        action="write:stage_id",
                        target_state="in_progress",
                        label="Reopen — move back to 'In Progress'",
                        description=(
                            "Reopen the ticket if the issue recurs. "
                            "Move back to the 'In Progress' stage via "
                            "update_record on stage_id."
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
                "by updating stage_id via update_record — NOT via "
                "execute_method (there are no Odoo-side action methods "
                "for ticket stage transitions)."
            ),
        ),
        version_notes={
            "17": "Stages and methods follow the v16 model — stage_id-driven.",
            "18": "Same as v17 — no new ticket action methods introduced.",
            "19": (
                "Same as v18. The gateway intentionally exposes "
                "'write:stage_id' transitions rather than synthetic "
                "method names so callers don't call non-existent "
                "Odoo methods like 'assign_ticket'."
            ),
        },
    )
