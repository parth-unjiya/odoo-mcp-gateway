"""Tests for WorkflowRegistry."""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)
from odoo_mcp_gateway.core.workflow.registry import WorkflowRegistry


def _make_workflow(model: str = "sale.order") -> WorkflowDef:
    """Create a minimal workflow for testing."""
    return WorkflowDef(
        model=model,
        display_name="Test Workflow",
        state_field="state",
        states={
            "draft": StateDef(
                name="draft",
                label="Draft",
                transitions=(
                    TransitionDef(
                        action="action_confirm",
                        target_state="done",
                        label="Confirm",
                        description="Confirms it.",
                    ),
                ),
            ),
            "done": StateDef(name="done", label="Done"),
        },
    )


def _make_workflow_with_guide() -> WorkflowDef:
    """Create a workflow with a create guide and version notes."""
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
                        label="Confirm",
                        description="Confirm the quotation.",
                    ),
                ),
            ),
            "sale": StateDef(name="sale", label="Sales Order"),
        },
        create_guide=CreateGuide(
            required_relations=(
                RelationHint(
                    field_name="partner_id",
                    relation_model="res.partner",
                    hint="Search for a customer",
                    required=True,
                ),
            ),
            recommended_fields=("date_order",),
            line_model="sale.order.line",
            line_field="order_line",
            notes="Add lines after creation.",
        ),
        version_notes={"19": "tax_id renamed to tax_ids"},
    )


# ------------------------------------------------------------------
# register / get
# ------------------------------------------------------------------


class TestRegisterAndGet:
    def test_register_and_get(self) -> None:
        reg = WorkflowRegistry()
        wf = _make_workflow("sale.order")
        reg.register(wf)
        assert reg.get("sale.order") is wf

    def test_get_returns_none_for_missing(self) -> None:
        reg = WorkflowRegistry()
        assert reg.get("nonexistent.model") is None

    def test_register_replaces_existing(self) -> None:
        reg = WorkflowRegistry()
        wf1 = _make_workflow("sale.order")
        wf2 = WorkflowDef(
            model="sale.order",
            display_name="Replaced",
            state_field="state",
            states={},
        )
        reg.register(wf1)
        reg.register(wf2)
        assert reg.get("sale.order") is wf2
        assert reg.get("sale.order").display_name == "Replaced"  # type: ignore[union-attr]

    def test_register_multiple_models(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow("sale.order"))
        reg.register(_make_workflow("purchase.order"))
        assert reg.get("sale.order") is not None
        assert reg.get("purchase.order") is not None


# ------------------------------------------------------------------
# list_models
# ------------------------------------------------------------------


class TestListModels:
    def test_empty_registry(self) -> None:
        reg = WorkflowRegistry()
        assert reg.list_models() == []

    def test_returns_sorted(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow("purchase.order"))
        reg.register(_make_workflow("sale.order"))
        reg.register(_make_workflow("crm.lead"))
        assert reg.list_models() == [
            "crm.lead",
            "purchase.order",
            "sale.order",
        ]


# ------------------------------------------------------------------
# load_stock_workflows
# ------------------------------------------------------------------


class TestLoadStockWorkflows:
    def test_loads_all_stock_workflows(self) -> None:
        reg = WorkflowRegistry()
        reg.load_stock_workflows()

        models = reg.list_models()
        assert "sale.order" in models
        assert "purchase.order" in models
        assert "hr.leave" in models
        assert "helpdesk.ticket" in models
        assert "crm.lead" in models
        assert len(models) == 5

    def test_stock_workflows_have_states(self) -> None:
        reg = WorkflowRegistry()
        reg.load_stock_workflows()

        for model in reg.list_models():
            wf = reg.get(model)
            assert wf is not None
            assert len(wf.states) > 0, f"{model} has no states"

    def test_stock_workflows_have_display_name(self) -> None:
        reg = WorkflowRegistry()
        reg.load_stock_workflows()

        for model in reg.list_models():
            wf = reg.get(model)
            assert wf is not None
            assert wf.display_name, f"{model} has no display_name"

    def test_stock_workflows_have_state_field(self) -> None:
        reg = WorkflowRegistry()
        reg.load_stock_workflows()

        for model in reg.list_models():
            wf = reg.get(model)
            assert wf is not None
            assert wf.state_field, f"{model} has no state_field"


# ------------------------------------------------------------------
# to_dict
# ------------------------------------------------------------------


class TestToDict:
    def test_returns_none_for_missing(self) -> None:
        reg = WorkflowRegistry()
        assert reg.to_dict("nonexistent") is None

    def test_minimal_workflow(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow("sale.order"))
        d = reg.to_dict("sale.order")

        assert d is not None
        assert d["model"] == "sale.order"
        assert d["display_name"] == "Test Workflow"
        assert d["state_field"] == "state"
        assert "draft" in d["states"]
        assert "done" in d["states"]

    def test_state_transitions_serialized(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow())
        d = reg.to_dict("sale.order")

        assert d is not None
        draft = d["states"]["draft"]
        assert len(draft["transitions"]) == 1
        t = draft["transitions"][0]
        assert t["action"] == "action_confirm"
        assert t["target_state"] == "done"
        assert t["label"] == "Confirm"
        assert t["description"] == "Confirms it."

    def test_done_state_has_no_transitions(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow())
        d = reg.to_dict("sale.order")

        assert d is not None
        assert d["states"]["done"]["transitions"] == []

    def test_create_guide_serialized(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow_with_guide())
        d = reg.to_dict("sale.order")

        assert d is not None
        assert "create_guide" in d
        guide = d["create_guide"]
        assert len(guide["required_relations"]) == 1
        rel = guide["required_relations"][0]
        assert rel["field_name"] == "partner_id"
        assert rel["relation_model"] == "res.partner"
        assert rel["required"] is True
        assert guide["recommended_fields"] == ["date_order"]
        assert guide["line_model"] == "sale.order.line"
        assert guide["line_field"] == "order_line"
        assert guide["notes"] == "Add lines after creation."

    def test_version_notes_serialized(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow_with_guide())
        d = reg.to_dict("sale.order")

        assert d is not None
        assert "version_notes" in d
        assert d["version_notes"]["19"] == "tax_id renamed to tax_ids"

    def test_no_create_guide_omits_key(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow())
        d = reg.to_dict("sale.order")

        assert d is not None
        assert "create_guide" not in d

    def test_no_version_notes_omits_key(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_make_workflow())
        d = reg.to_dict("sale.order")

        assert d is not None
        assert "version_notes" not in d

    def test_all_stock_workflows_serialize(self) -> None:
        """Ensure every stock workflow can be serialized without error."""
        reg = WorkflowRegistry()
        reg.load_stock_workflows()

        for model in reg.list_models():
            d = reg.to_dict(model)
            assert d is not None
            assert d["model"] == model
            assert isinstance(d["states"], dict)
