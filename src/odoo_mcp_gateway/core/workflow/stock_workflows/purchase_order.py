"""Workflow definition for purchase.order (Purchase Order)."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)


def get_workflow() -> WorkflowDef:
    """Return the purchase.order workflow definition.

    State machine: draft -> sent -> purchase -> done / cancel
    """
    return WorkflowDef(
        model="purchase.order",
        display_name="Purchase Order",
        state_field="state",
        states={
            "draft": StateDef(
                name="draft",
                label="RFQ",
                transitions=(
                    TransitionDef(
                        action="button_confirm",
                        target_state="purchase",
                        label="Confirm Order",
                        description=(
                            "Confirms the RFQ, converting it into a "
                            "purchase order. Requires at least one "
                            "order line."
                        ),
                    ),
                    TransitionDef(
                        action="button_cancel",
                        target_state="cancel",
                        label="Cancel",
                        description="Cancels the request for quotation.",
                    ),
                ),
            ),
            "sent": StateDef(
                name="sent",
                label="RFQ Sent",
                transitions=(
                    TransitionDef(
                        action="button_confirm",
                        target_state="purchase",
                        label="Confirm Order",
                        description=(
                            "Confirms the sent RFQ, converting it into "
                            "a purchase order."
                        ),
                    ),
                    TransitionDef(
                        action="button_cancel",
                        target_state="cancel",
                        label="Cancel",
                        description="Cancels the sent request for quotation.",
                    ),
                ),
            ),
            "purchase": StateDef(
                name="purchase",
                label="Purchase Order",
                transitions=(
                    TransitionDef(
                        action="button_done",
                        target_state="done",
                        label="Lock",
                        description=(
                            "Locks the purchase order, preventing "
                            "further modifications."
                        ),
                    ),
                    TransitionDef(
                        action="button_cancel",
                        target_state="cancel",
                        label="Cancel",
                        description="Cancels the confirmed purchase order.",
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
                        action="button_draft",
                        target_state="draft",
                        label="Set to Draft",
                        description=(
                            "Resets a cancelled order back to draft RFQ state."
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
                        "Search res.partner for the vendor. "
                        "Use search_read with domain "
                        "[['supplier_rank', '>', 0]] to find vendors."
                    ),
                    required=True,
                ),
            ),
            recommended_fields=(
                "date_order",
                "date_planned",
                "currency_id",
                "user_id",
            ),
            line_model="purchase.order.line",
            line_field="order_line",
            notes=(
                "After creating the purchase order, add at least one order "
                "line via purchase.order.line with product_id, product_qty, "
                "and price_unit. Then call button_confirm to confirm."
            ),
        ),
        version_notes={},
    )
