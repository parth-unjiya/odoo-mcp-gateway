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
