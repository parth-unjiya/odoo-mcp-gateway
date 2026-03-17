"""Tests for workflow tools (get_create_requirements, get_record_actions)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.discovery.models import FieldInfo
from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry
from odoo_mcp_gateway.core.workflow.stock_workflows.sale_order import (
    get_workflow as get_sale_order_workflow,
)
from odoo_mcp_gateway.tools.workflow import register_workflow_tools

from .conftest import make_gateway, make_mock_client

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_registry_with_sale_order() -> WorkflowRegistry:
    """Create a workflow registry with the sale.order workflow loaded."""
    reg = WorkflowRegistry()
    reg.register(get_sale_order_workflow())
    return reg


def _get_tools(
    gateway: Any,
    registry: WorkflowRegistry | None = None,
) -> dict[str, Any]:
    """Register workflow tools on a test server and extract the functions."""
    if registry is None:
        registry = _make_registry_with_sale_order()
    server = FastMCP(name="test")
    register_workflow_tools(server, gateway, registry)
    tools: dict[str, Any] = {}
    for name, tool in server._tool_manager._tools.items():
        tools[name] = tool.fn
    return tools


# ------------------------------------------------------------------
# get_create_requirements
# ------------------------------------------------------------------


class TestGetCreateRequirements:
    async def test_returns_required_fields(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        # Populate field inspector cache
        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "partner_id": FieldInfo(
                    name="partner_id",
                    field_type="many2one",
                    string="Customer",
                    required=True,
                    relation="res.partner",
                ),
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Order Reference",
                    required=False,
                    readonly=True,
                ),
                "date_order": FieldInfo(
                    name="date_order",
                    field_type="datetime",
                    string="Order Date",
                    required=False,
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "error" not in resp
        assert resp["model"] == "sale.order"
        assert "partner_id" in resp["required_fields"]
        assert resp["required_fields"]["partner_id"]["type"] == "many2one"
        # readonly field should be excluded
        assert "name" not in resp["required_fields"]

    async def test_includes_workflow_guide(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "partner_id": FieldInfo(
                    name="partner_id",
                    field_type="many2one",
                    string="Customer",
                    required=True,
                    relation="res.partner",
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "relation_hints" in resp
        assert len(resp["relation_hints"]) >= 1
        partner_hint = resp["relation_hints"][0]
        assert partner_hint["field_name"] == "partner_id"
        assert partner_hint["relation_model"] == "res.partner"

        assert "recommended_fields" in resp
        assert "line_model" in resp
        assert resp["line_model"] == "sale.order.line"
        assert "notes" in resp

    async def test_includes_version_notes(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "partner_id": FieldInfo(
                    name="partner_id",
                    field_type="many2one",
                    string="Customer",
                    required=True,
                    relation="res.partner",
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "version_notes" in resp
        assert "19" in resp["version_notes"]

    async def test_model_without_workflow(self) -> None:
        """Model not in workflow registry should still return fields."""
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["res.partner"] = (
            999999999.0,
            {
                "name": FieldInfo(
                    name="name",
                    field_type="char",
                    string="Name",
                    required=True,
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="res.partner")

        assert "error" not in resp
        assert resp["model"] == "res.partner"
        assert "name" in resp["required_fields"]
        # No workflow guide
        assert "relation_hints" not in resp
        assert "line_model" not in resp

    async def test_restricted_model_returns_error(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](
            model="ir.config_parameter"
        )

        assert "error" in resp
        assert "always blocked" in resp["error"]

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "error" in resp
        assert "Not authenticated" in resp["error"]

    async def test_invalid_model_returns_error(self) -> None:
        gateway = make_gateway()

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="INVALID MODEL!")

        assert "error" in resp

    async def test_excludes_readonly_fields(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "amount_total": FieldInfo(
                    name="amount_total",
                    field_type="monetary",
                    string="Total",
                    required=False,
                    readonly=True,
                ),
                "note": FieldInfo(
                    name="note",
                    field_type="text",
                    string="Notes",
                    required=False,
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "amount_total" not in resp["required_fields"]
        assert resp["optional_field_count"] >= 1

    async def test_excludes_binary_fields(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "image": FieldInfo(
                    name="image",
                    field_type="binary",
                    string="Image",
                    required=False,
                    is_binary=True,
                ),
                "note": FieldInfo(
                    name="note",
                    field_type="text",
                    string="Notes",
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "image" not in resp.get("required_fields", {})

    async def test_includes_selection_options(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        gateway.field_inspector._cache["sale.order"] = (
            999999999.0,
            {
                "type": FieldInfo(
                    name="type",
                    field_type="selection",
                    string="Type",
                    required=True,
                    selection=[("sale", "Sale"), ("refund", "Refund")],
                ),
            },
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "type" in resp["required_fields"]
        assert "selection" in resp["required_fields"]["type"]

    async def test_read_only_model_returns_error(self) -> None:
        """A read-only model should block create requirements."""
        gateway = make_gateway(
            model_access_config=ModelAccessConfig(
                default_policy="deny",
                stock_models={"read_only": ["sale.order"]},
            ),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_create_requirements"](model="sale.order")

        assert "error" in resp
        assert "read-only" in resp["error"]


# ------------------------------------------------------------------
# get_record_actions
# ------------------------------------------------------------------


class TestGetRecordActions:
    def _sale_order_access(self) -> ModelAccessConfig:
        """Model access config with sale.order methods allowed."""
        return ModelAccessConfig(
            default_policy="allow",
            stock_models={"full_crud": ["sale.order"]},
            allowed_methods={
                "sale.order": [
                    "action_confirm",
                    "action_cancel",
                    "action_done",
                    "action_draft",
                ],
            },
        )

    async def test_returns_actions_for_draft_sale_order(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "draft"}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=self._sale_order_access(),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        assert "error" not in resp
        assert resp["model"] == "sale.order"
        assert resp["record_id"] == 1
        assert resp["has_workflow"] is True
        assert resp["current_state"] == "draft"
        assert resp["current_state_label"] == "Quotation"
        assert len(resp["actions"]) >= 1

        action_methods = [a["method"] for a in resp["actions"]]
        assert "action_confirm" in action_methods

    async def test_returns_actions_for_sale_state(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "sale"}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=self._sale_order_access(),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        assert resp["current_state"] == "sale"
        assert resp["current_state_label"] == "Sales Order"
        action_methods = [a["method"] for a in resp["actions"]]
        assert "action_done" in action_methods

    async def test_done_state_has_no_actions(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "done"}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=self._sale_order_access(),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        assert resp["current_state"] == "done"
        assert resp["actions"] == []

    async def test_model_without_workflow(self) -> None:
        mock_client = make_mock_client()
        gateway = make_gateway(mock_client=mock_client)

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="res.partner", record_id=1
        )

        assert resp["has_workflow"] is False
        assert "message" in resp

    async def test_record_not_found(self) -> None:
        mock_client = make_mock_client(execute_kw_return=[])
        gateway = make_gateway(mock_client=mock_client)

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=999
        )

        assert "error" in resp
        assert "not found" in resp["error"]

    async def test_invalid_record_id(self) -> None:
        gateway = make_gateway()

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=0
        )

        assert "error" in resp
        assert "positive" in resp["error"]

    async def test_negative_record_id(self) -> None:
        gateway = make_gateway()

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=-1
        )

        assert "error" in resp

    async def test_not_authenticated_returns_error(self) -> None:
        gateway = make_gateway()
        gateway.auth_managers.clear()

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        assert "error" in resp
        assert "Not authenticated" in resp["error"]

    async def test_restricted_model_returns_error(self) -> None:
        gateway = make_gateway(
            restriction_config=RestrictionConfig(
                always_blocked=["ir.config_parameter"],
            ),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="ir.config_parameter", record_id=1
        )

        assert "error" in resp

    async def test_unknown_state_returns_empty_actions(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "unknown_state"}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=self._sale_order_access(),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        assert resp["has_workflow"] is True
        assert resp["current_state"] == "unknown_state"
        assert resp["actions"] == []
        assert "message" in resp

    async def test_stage_based_workflow(self) -> None:
        """Test with crm.lead which uses stage_id (many2one)."""
        from odoo_mcp_gateway.core.workflow.stock_workflows.crm_lead import (
            get_workflow as get_crm_workflow,
        )

        registry = WorkflowRegistry()
        registry.register(get_crm_workflow())

        # Many2one returns [id, display_name]
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "stage_id": [1, "New"]}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=ModelAccessConfig(
                default_policy="allow",
                stock_models={"full_crud": ["crm.lead"]},
                allowed_methods={
                    "crm.lead": [
                        "convert_opportunity",
                        "action_set_won",
                        "action_set_lost",
                        "toggle_active",
                    ],
                },
            ),
        )

        tools = _get_tools(gateway, registry=registry)
        resp = await tools["get_record_actions"](
            model="crm.lead", record_id=1
        )

        assert resp["has_workflow"] is True
        assert resp.get("stage_based") is True
        assert resp["current_state_label"] == "New"
        assert "message" in resp
        assert len(resp["actions"]) >= 1

    async def test_actions_filtered_by_method_restrictions(self) -> None:
        """Methods blocked by restriction checker should not appear."""
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "draft"}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            model_access_config=ModelAccessConfig(
                default_policy="allow",
                stock_models={"full_crud": ["sale.order"]},
                allowed_methods={
                    # Only allow action_cancel, not action_confirm
                    "sale.order": ["action_cancel"],
                },
            ),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        action_methods = [a["method"] for a in resp["actions"]]
        # action_confirm is not in allowed_methods, so non-admin can't see it
        assert "action_confirm" not in action_methods
        assert "action_cancel" in action_methods

    async def test_admin_sees_all_actions(self) -> None:
        """Admin should see all actions even without explicit allowed_methods."""
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "draft"}]
        )
        gateway = make_gateway(
            mock_client=mock_client,
            is_admin=True,
            model_access_config=ModelAccessConfig(
                default_policy="allow",
                stock_models={"full_crud": ["sale.order"]},
            ),
        )

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        action_methods = [a["method"] for a in resp["actions"]]
        assert "action_confirm" in action_methods
        assert "action_cancel" in action_methods

    async def test_action_has_required_keys(self) -> None:
        mock_client = make_mock_client(
            execute_kw_return=[{"id": 1, "state": "draft"}]
        )
        gateway = make_gateway(mock_client=mock_client, is_admin=True)

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="sale.order", record_id=1
        )

        for action in resp["actions"]:
            assert "method" in action
            assert "label" in action
            assert "description" in action
            assert "target_state" in action

    async def test_invalid_model_returns_error(self) -> None:
        gateway = make_gateway()

        tools = _get_tools(gateway)
        resp = await tools["get_record_actions"](
            model="INVALID!", record_id=1
        )

        assert "error" in resp
