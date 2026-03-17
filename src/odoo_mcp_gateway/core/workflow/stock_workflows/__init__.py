"""Stock workflow definitions for common Odoo models."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import WorkflowDef

from .crm_lead import get_workflow as get_crm_lead_workflow
from .helpdesk_ticket import get_workflow as get_helpdesk_ticket_workflow
from .hr_leave import get_workflow as get_hr_leave_workflow
from .purchase_order import get_workflow as get_purchase_order_workflow
from .sale_order import get_workflow as get_sale_order_workflow

__all__ = [
    "get_all_stock_workflows",
    "get_crm_lead_workflow",
    "get_helpdesk_ticket_workflow",
    "get_hr_leave_workflow",
    "get_purchase_order_workflow",
    "get_sale_order_workflow",
]


def get_all_stock_workflows() -> list[WorkflowDef]:
    """Return all built-in stock workflow definitions."""
    return [
        get_sale_order_workflow(),
        get_purchase_order_workflow(),
        get_hr_leave_workflow(),
        get_helpdesk_ticket_workflow(),
        get_crm_lead_workflow(),
    ]
