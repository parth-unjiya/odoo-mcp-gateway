"""Workflow definition for sale.order (Sales Order / Quotation)."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)


def get_workflow() -> WorkflowDef:
    """Return the sale.order workflow definition.

    State machine: draft -> sent -> sale -> done / cancel
    """
    return WorkflowDef(
        model="sale.order",
        display_name="Sales Order",
        state_field="state",
        states={
            "draft": StateDef(
                name="draft",
                label="Quotation",
                transitions=(
                    TransitionDef(
                        action="action_confirm",
                        target_state="sale",
                        label="Confirm Order",
                        description=(
                            "Confirms the quotation, converting it into a "
                            "sales order. Requires at least one order line."
                        ),
                    ),
                    TransitionDef(
                        action="action_cancel",
                        target_state="cancel",
                        label="Cancel",
                        description="Cancels the quotation.",
                    ),
                ),
            ),
            "sent": StateDef(
                name="sent",
                label="Quotation Sent",
                transitions=(
                    TransitionDef(
                        action="action_confirm",
                        target_state="sale",
                        label="Confirm Order",
                        description=(
                            "Confirms the sent quotation, converting it "
                            "into a sales order."
                        ),
                    ),
                    TransitionDef(
                        action="action_cancel",
                        target_state="cancel",
                        label="Cancel",
                        description="Cancels the quotation.",
                    ),
                ),
            ),
            "sale": StateDef(
                name="sale",
                label="Sales Order",
                transitions=(
                    TransitionDef(
                        action="action_done",
                        target_state="done",
                        label="Lock",
                        description=(
                            "Locks the sales order, preventing further "
                            "modifications."
                        ),
                    ),
                    TransitionDef(
                        action="action_cancel",
                        target_state="cancel",
                        label="Cancel",
                        description="Cancels the confirmed sales order.",
                    ),
                ),
            ),
            "done": StateDef(
                name="done",
                label="Locked",
                transitions=(),
            ),
            "cancel": StateDef(
                name="cancel",
                label="Cancelled",
                transitions=(
                    TransitionDef(
                        action="action_draft",
                        target_state="draft",
                        label="Set to Quotation",
                        description=(
                            "Resets a cancelled order back to draft "
                            "quotation state."
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
                        "Search res.partner for the customer. "
                        "Use search_read with domain "
                        "[['customer_rank', '>', 0]] to find customers."
                    ),
                    required=True,
                ),
            ),
            recommended_fields=(
                "date_order",
                "validity_date",
                "payment_term_id",
                "pricelist_id",
                "user_id",
            ),
            line_model="sale.order.line",
            line_field="order_line",
            notes=(
                "After creating the sale order, add at least one order line "
                "via sale.order.line with product_id and product_uom_qty. "
                "Then call action_confirm to convert to a sales order."
            ),
        ),
        version_notes={
            "19": (
                "In Odoo 19, sale.order.line field 'tax_id' was renamed to "
                "'tax_ids', and 'product_uom' was renamed to 'product_uom_id'."
            ),
        },
    )
