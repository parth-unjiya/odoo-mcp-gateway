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
                    # action_lock is the v18+/v19 method name; action_done
                    # is the v17-only legacy alias. Version constraints make
                    # _filter_transitions emit only the one that matches the
                    # detected Odoo version, so callers never see a method
                    # that will raise AttributeError on their server.
                    TransitionDef(
                        action="action_lock",
                        target_state="done",
                        label="Lock",
                        description=(
                            "Locks the sales order, preventing further "
                            "modifications. Available on Odoo 18 and 19."
                        ),
                        min_version=18,
                    ),
                    TransitionDef(
                        action="action_done",
                        target_state="done",
                        label="Lock (v17)",
                        description=(
                            "Locks the sales order on Odoo 17. On Odoo "
                            "18+ the method was renamed to action_lock."
                        ),
                        max_version=17,
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
                            "Resets a cancelled order back to draft quotation state."
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
            "17": (
                "On Odoo 17, lock the sales order with action_done. The "
                "v18+ alias action_lock does not exist on v17."
            ),
            "18": (
                "On Odoo 18+, the lock method was renamed to action_lock. "
                "action_done still exists as a backwards-compatible alias "
                "on early v18 builds but should be considered deprecated."
            ),
            "19": (
                "On Odoo 19, use action_lock to lock the order — "
                "action_done was removed. The sale.order.line field "
                "'tax_id' was also renamed to 'tax_ids', and "
                "'product_uom' was renamed to 'product_uom_id'."
            ),
        },
    )
