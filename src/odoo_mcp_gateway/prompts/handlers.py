"""MCP Prompt handlers — reusable AI interaction templates."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_prompts(server: FastMCP, get_context: Any) -> None:
    """Register all MCP prompts on the server."""

    @server.prompt()
    async def analyze_model(model: str) -> str:
        """Analyze an Odoo model's structure, fields, and relationships.

        Provides a comprehensive prompt for understanding any model.
        """
        return (
            f"Analyze the Odoo model '{model}'. Follow these steps:\n"
            "\n"
            f"1. Use the get_model_fields tool to retrieve the field "
            f"definitions for '{model}'\n"
            "2. Identify the key fields:\n"
            "   - Required fields (must be filled when creating records)\n"
            "   - Status/state fields (workflow stages)\n"
            "   - Relational fields (connections to other models)\n"
            "   - Computed fields (auto-calculated, read-only)\n"
            "3. Describe the model's purpose based on its fields\n"
            "4. List the available actions/methods if any "
            "(use execute_method patterns)\n"
            "5. Show an example of how to create a record with minimum "
            "required fields\n"
            "6. Show an example search_read query with useful filters"
        )

    @server.prompt()
    async def explore_data(
        model: str,
        question: str = "What data is available?",
    ) -> str:
        """Explore data in an Odoo model based on a natural language question."""
        return (
            f'Answer this question about {model}: "{question}"\n'
            "\n"
            "Steps:\n"
            f"1. First, use get_model_fields to understand what fields "
            f"'{model}' has\n"
            "2. Build an appropriate domain filter based on the question\n"
            "3. Use search_read with relevant fields and filters to find "
            "the data\n"
            "4. Use search_count to know the total matching records\n"
            "5. If the question involves aggregation (totals, averages, "
            "counts by group), use read_group\n"
            "6. Present the results in a clear, organized format\n"
            "7. Suggest follow-up questions the user might find useful"
        )

    @server.prompt()
    async def create_workflow(
        model: str,
        action: str = "complete the standard workflow",
    ) -> str:
        """Guide through a workflow on an Odoo model."""
        return (
            f"Help me {action} on the model '{model}'.\n"
            "\n"
            "Steps:\n"
            "1. Use get_model_fields to find the state/stage field "
            "and its options\n"
            "2. Use search_read to find records in the starting state\n"
            "3. For each workflow step:\n"
            "   a. Explain what the step does\n"
            "   b. Show the execute_method call needed "
            "(e.g., action_confirm, action_validate)\n"
            "   c. Verify the state changed after execution\n"
            "4. If any step fails, explain why and suggest fixes\n"
            "5. Summarize the complete workflow path taken"
        )

    @server.prompt()
    async def compare_records(
        model: str,
        record_ids: str = "",
    ) -> str:
        """Compare two or more records from the same model."""
        ids_note = f" (IDs: {record_ids})" if record_ids else ""
        return (
            f"Compare records from '{model}'{ids_note}.\n"
            "\n"
            "Steps:\n"
            "1. Use get_model_fields to identify comparable fields\n"
            "2. "
            + (
                f"Use get_record for each ID: {record_ids}"
                if record_ids
                else "Use search_read to find records to compare"
            )
            + "\n"
            "3. Create a comparison table showing differences\n"
            "4. Highlight key differences (amounts, dates, statuses)\n"
            "5. Note any relational differences "
            "(different partners, users, etc.)\n"
            "6. Summarize the most significant differences"
        )

    @server.prompt()
    async def generate_report(
        model: str,
        period: str = "this month",
        focus: str = "overview",
    ) -> str:
        """Generate an analytical report from Odoo data."""
        return (
            f"Generate a {focus} report for '{model}' covering {period}.\n"
            "\n"
            "Steps:\n"
            "1. Use get_model_fields to find date, amount, "
            "and status fields\n"
            f"2. Build date filters for '{period}' "
            "(use create_date or date_order as appropriate)\n"
            "3. Use search_count to get total records in the period\n"
            "4. Use read_group to aggregate:\n"
            "   - By status/state (how many in each stage)\n"
            "   - By date (daily/weekly/monthly trends)\n"
            "   - By amount (totals, averages)\n"
            "   - By key relations (top partners, users, etc.)\n"
            "5. Use search_read for the top/bottom records "
            "(highest amounts, most recent, etc.)\n"
            "6. Present findings as a structured report with:\n"
            "   - Summary statistics\n"
            "   - Trend analysis\n"
            "   - Notable records\n"
            "   - Recommendations based on the data"
        )

    @server.prompt()
    async def discover_custom_modules() -> str:
        """Discover and understand custom Odoo modules installed."""
        return (
            "Explore custom modules on this Odoo instance.\n"
            "\n"
            "Steps:\n"
            "1. Use list_models with include_custom=true to find all "
            "custom models\n"
            "2. Group custom models by their module prefix "
            "(e.g., custom.delivery.*, x_studio_*)\n"
            "3. For each custom module group:\n"
            "   a. Use get_model_fields on the main model to understand "
            "its purpose\n"
            "   b. Identify related models (via relational fields)\n"
            "   c. List available actions "
            "(from the allowed_methods configuration)\n"
            "4. Create a summary table:\n"
            "   | Module | Models | Purpose | Key Actions |\n"
            "5. Suggest useful queries for each discovered module\n"
            "6. Note any modules that appear to need configuration "
            "in model_access.yaml"
        )

    @server.prompt()
    async def debug_access(
        model: str = "",
        operation: str = "read",
    ) -> str:
        """Debug access issues for a model."""
        target = f"'{model}'" if model else "the problematic model"
        return (
            f"Debug access issues for {target} ({operation} operation).\n"
            "\n"
            "Steps:\n"
            f"1. Check if {target} appears in list_models output\n"
            "   - If not: it may be in restrictions.yaml always_blocked "
            "or not in model_access.yaml\n"
            f"2. Try search_read on {target} with a simple domain\n"
            '   - If "Access denied": check Odoo user\'s groups '
            "and ir.model.access rules\n"
            '   - If "not accessible through gateway": model is blocked '
            "in restrictions.yaml\n"
            '   - If "requires administrator": model is in admin_only '
            "list\n"
            "3. For write operations:\n"
            "   - Check if model is in admin_write_only "
            "(read OK, write needs admin)\n"
            "   - Check blocked_write_fields for specific field issues\n"
            "4. For execute_method:\n"
            "   - Verify the method is in allowed_methods for this model\n"
            "   - Check if method starts with '_' "
            "(private = admin only)\n"
            "5. Summarize the issue and suggest the specific YAML "
            "config change needed"
        )

    # ------------------------------------------------------------------
    # Workflow prompts (v2 Intelligent Workflow Engine)
    # ------------------------------------------------------------------

    @server.prompt()
    async def quote_to_invoice() -> str:
        """Guide through the full sales cycle from quotation to invoice.

        Walks through: create quotation, add lines, confirm, create
        invoice, and register payment.
        """
        return (
            "Guide me through the full sales cycle in Odoo.\n"
            "\n"
            "Steps:\n"
            "1. Use get_create_requirements on 'sale.order' to see "
            "what fields are needed\n"
            "2. Search res.partner for a customer: "
            "search_read with [['customer_rank', '>', 0]]\n"
            "3. Create the quotation via create_record on 'sale.order' "
            "with partner_id\n"
            "4. Use get_create_requirements on 'sale.order.line' for "
            "line item fields\n"
            "5. Search product.product for products, then add order "
            "lines via create_record on 'sale.order.line'\n"
            "6. Use get_record_actions on the quotation to see "
            "available transitions\n"
            "7. Confirm the order: execute_method 'action_confirm' on "
            "'sale.order'\n"
            "8. Verify the state changed to 'sale' via get_record\n"
            "9. Create the invoice: execute_method "
            "'_create_invoices' on 'sale.order' (admin only)\n"
            "10. Register payment on the invoice if needed\n"
            "\n"
            "At each step, use get_record_actions to show what "
            "transitions are available. If any step fails, explain "
            "why and how to fix it."
        )

    @server.prompt()
    async def employee_onboarding() -> str:
        """Guide through HR employee onboarding in Odoo.

        Walks through: create employee, set department and job,
        assign to projects.
        """
        return (
            "Guide me through onboarding a new employee in Odoo.\n"
            "\n"
            "Steps:\n"
            "1. Use get_create_requirements on 'hr.employee' to see "
            "required fields\n"
            "2. Search hr.department for departments: "
            "search_read on 'hr.department'\n"
            "3. Search hr.job for job positions: "
            "search_read on 'hr.job'\n"
            "4. Create the employee via create_record on "
            "'hr.employee' with name, department_id, job_id, "
            "and work_email\n"
            "5. Verify creation via get_record on the new employee\n"
            "6. Optionally assign to projects:\n"
            "   a. Search project.project for active projects\n"
            "   b. Create project tasks or add to project team\n"
            "7. Set up leave allocation:\n"
            "   a. Use get_create_requirements on 'hr.leave.allocation'\n"
            "   b. Create leave allocations for the employee\n"
            "8. Summarize the onboarding: employee name, department, "
            "job, projects assigned, leave allocations"
        )

    @server.prompt()
    async def ticket_lifecycle() -> str:
        """Guide through helpdesk ticket lifecycle.

        Walks through: create ticket, assign, work, resolve, close.
        """
        return (
            "Guide me through the helpdesk ticket lifecycle in Odoo.\n"
            "\n"
            "Steps:\n"
            "1. Use get_create_requirements on 'helpdesk.ticket' to "
            "see required fields\n"
            "2. Search helpdesk.team for available support teams: "
            "search_read on 'helpdesk.team'\n"
            "3. Create the ticket via create_record on "
            "'helpdesk.ticket' with name, team_id, and description\n"
            "4. Use get_record_actions on the ticket to see available "
            "actions\n"
            "5. Assign the ticket: update_record to set user_id\n"
            "6. Discover available stages: search_read on "
            "'helpdesk.stage' with [['team_ids', 'in', [team_id]]]\n"
            "7. Progress through stages by updating stage_id:\n"
            "   a. Move to 'In Progress' when work begins\n"
            "   b. Move to 'Solved' when resolved\n"
            "   c. Move to 'Closed' / 'Done' to finalize\n"
            "8. At each stage, verify the change via get_record\n"
            "9. If the issue recurs, show how to reopen the ticket\n"
            "\n"
            "Note: Helpdesk stages are configurable per team. "
            "Always discover actual stages first."
        )

    @server.prompt()
    async def purchase_to_receipt() -> str:
        """Guide through purchase cycle from PO to receipt.

        Walks through: create PO, confirm, receive products, validate.
        """
        return (
            "Guide me through the purchase cycle in Odoo.\n"
            "\n"
            "Steps:\n"
            "1. Use get_create_requirements on 'purchase.order' to "
            "see what fields are needed\n"
            "2. Search res.partner for a vendor: "
            "search_read with [['supplier_rank', '>', 0]]\n"
            "3. Create the PO via create_record on 'purchase.order' "
            "with partner_id\n"
            "4. Use get_create_requirements on 'purchase.order.line' "
            "for line item fields\n"
            "5. Search product.product for products, then add order "
            "lines via create_record on 'purchase.order.line'\n"
            "6. Use get_record_actions on the PO to see available "
            "transitions\n"
            "7. Confirm the order: execute_method 'button_confirm' on "
            "'purchase.order'\n"
            "8. Verify the state changed to 'purchase' via get_record\n"
            "9. Check for incoming receipts: search_read on "
            "'stock.picking' with [['origin', '=', po_name]]\n"
            "10. Validate the receipt when products arrive: "
            "execute_method 'button_validate' on 'stock.picking'\n"
            "\n"
            "At each step, use get_record_actions to show what "
            "transitions are available."
        )

    @server.prompt()
    async def lead_to_opportunity() -> str:
        """Guide through CRM pipeline from lead to won/lost.

        Walks through: create lead, qualify, convert to opportunity,
        mark as won or lost.
        """
        return (
            "Guide me through the CRM pipeline in Odoo.\n"
            "\n"
            "Steps:\n"
            "1. Use get_create_requirements on 'crm.lead' to see "
            "what fields are needed\n"
            "2. Create a lead via create_record on 'crm.lead' with "
            "name, type='lead', and contact info\n"
            "3. Use get_record_actions on the lead to see available "
            "actions\n"
            "4. Qualify the lead by updating fields:\n"
            "   a. Set partner_id (search res.partner first)\n"
            "   b. Set expected_revenue and probability\n"
            "5. Convert to opportunity: execute_method "
            "'convert_opportunity' on 'crm.lead'\n"
            "6. Progress through CRM stages:\n"
            "   a. Search crm.stage to discover available stages\n"
            "   b. Update stage_id via update_record to advance\n"
            "7. Close the opportunity:\n"
            "   a. Won: execute_method 'action_set_won' on 'crm.lead'\n"
            "   b. Lost: execute_method 'action_set_lost' on "
            "'crm.lead' with optional lost_reason_id\n"
            "8. Verify final state via get_record\n"
            "\n"
            "At each step, use get_record_actions to show what "
            "transitions are available. Use the odoo://workflow/"
            "crm.lead resource for the full state machine."
        )
