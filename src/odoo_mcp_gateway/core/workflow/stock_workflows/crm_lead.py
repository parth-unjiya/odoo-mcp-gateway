"""Workflow definition for crm.lead (CRM Lead / Opportunity)."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)


def get_workflow() -> WorkflowDef:
    """Return the crm.lead workflow definition.

    CRM leads use stage_id (many2one to crm.stage) rather than a fixed
    selection field. Stages are configurable per sales team. The ``type``
    field distinguishes leads (``lead``) from opportunities (``opportunity``).

    Key methods:
    - convert_opportunity: converts a lead into an opportunity
    - action_set_won: marks the opportunity as won
    - action_set_lost: marks the opportunity as lost
    """
    return WorkflowDef(
        model="crm.lead",
        display_name="CRM Lead / Opportunity",
        state_field="stage_id",
        states={
            "new": StateDef(
                name="new",
                label="New",
                transitions=(
                    TransitionDef(
                        action="convert_opportunity",
                        target_state="qualified",
                        label="Convert to Opportunity",
                        description=(
                            "Converts a lead into an opportunity. "
                            "Requires partner_id to be set. Call "
                            "execute_method with method "
                            "'convert_opportunity' and partner_id in "
                            "args."
                        ),
                    ),
                ),
            ),
            "qualified": StateDef(
                name="qualified",
                label="Qualified",
                transitions=(
                    TransitionDef(
                        action="action_set_won",
                        target_state="won",
                        label="Won",
                        description=(
                            "Marks the opportunity as won. This sets "
                            "the probability to 100% and moves to the "
                            "Won stage."
                        ),
                    ),
                    TransitionDef(
                        action="action_set_lost",
                        target_state="lost",
                        label="Lost",
                        description=(
                            "Marks the opportunity as lost. Optionally "
                            "provide a lost_reason_id."
                        ),
                    ),
                ),
            ),
            "proposition": StateDef(
                name="proposition",
                label="Proposition",
                transitions=(
                    TransitionDef(
                        action="action_set_won",
                        target_state="won",
                        label="Won",
                        description="Marks the opportunity as won.",
                    ),
                    TransitionDef(
                        action="action_set_lost",
                        target_state="lost",
                        label="Lost",
                        description="Marks the opportunity as lost.",
                    ),
                ),
            ),
            "won": StateDef(
                name="won",
                label="Won",
                transitions=(),
            ),
            "lost": StateDef(
                name="lost",
                label="Lost",
                transitions=(
                    TransitionDef(
                        action="toggle_active",
                        target_state="new",
                        label="Restore",
                        description=(
                            "Restores a lost lead/opportunity. Re-"
                            "activates the record by toggling the "
                            "active field."
                        ),
                    ),
                ),
            ),
        },
        create_guide=CreateGuide(
            required_relations=(
                RelationHint(
                    field_name="partner_id",
                    relation_model="res.partner",
                    hint=(
                        "Search res.partner for the prospect/customer. "
                        "Optional for leads, recommended for "
                        "opportunities."
                    ),
                    required=False,
                ),
            ),
            recommended_fields=(
                "name",
                "type",
                "user_id",
                "team_id",
                "expected_revenue",
                "probability",
                "email_from",
                "phone",
            ),
            notes=(
                "Set type='lead' for a new lead or type='opportunity' "
                "for a direct opportunity. Stages are managed via "
                "stage_id (many2one to crm.stage). Use search_read on "
                "crm.stage to discover available stages. Move between "
                "stages by updating stage_id via update_record. Use "
                "action_set_won/action_set_lost for final disposition."
            ),
        ),
        version_notes={
            "19": (
                "In Odoo 19, the 'mobile' field was removed from "
                "crm.lead. A new 'won_status' field was added."
            ),
        },
    )
