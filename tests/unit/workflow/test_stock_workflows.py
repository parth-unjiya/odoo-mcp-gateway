"""Tests for stock workflow definitions."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.workflow.definitions import WorkflowDef
from odoo_mcp_gateway.core.workflow.stock_workflows import (
    get_all_stock_workflows,
    get_crm_lead_workflow,
    get_helpdesk_ticket_workflow,
    get_hr_leave_workflow,
    get_purchase_order_workflow,
    get_sale_order_workflow,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_all_transitions(wf: WorkflowDef) -> list[str]:
    """Collect all action names from all states."""
    actions: list[str] = []
    for state_def in wf.states.values():
        for t in state_def.transitions:
            actions.append(t.action)
    return actions


def _get_all_target_states(wf: WorkflowDef) -> set[str]:
    """Collect all target states referenced in transitions."""
    targets: set[str] = set()
    for state_def in wf.states.values():
        for t in state_def.transitions:
            targets.add(t.target_state)
    return targets


# ------------------------------------------------------------------
# get_all_stock_workflows
# ------------------------------------------------------------------


class TestGetAllStockWorkflows:
    def test_returns_five_workflows(self) -> None:
        workflows = get_all_stock_workflows()
        assert len(workflows) == 5

    def test_all_have_unique_models(self) -> None:
        workflows = get_all_stock_workflows()
        models = [wf.model for wf in workflows]
        assert len(models) == len(set(models))

    def test_expected_models(self) -> None:
        workflows = get_all_stock_workflows()
        models = {wf.model for wf in workflows}
        assert models == {
            "sale.order",
            "purchase.order",
            "hr.leave",
            "helpdesk.ticket",
            "crm.lead",
        }


# ------------------------------------------------------------------
# sale.order
# ------------------------------------------------------------------


class TestSaleOrderWorkflow:
    def test_model(self) -> None:
        wf = get_sale_order_workflow()
        assert wf.model == "sale.order"
        assert wf.display_name == "Sales Order"
        assert wf.state_field == "state"

    def test_has_expected_states(self) -> None:
        wf = get_sale_order_workflow()
        expected = {"draft", "sent", "sale", "done", "cancel"}
        assert set(wf.states.keys()) == expected

    def test_draft_transitions(self) -> None:
        wf = get_sale_order_workflow()
        draft = wf.states["draft"]
        actions = [t.action for t in draft.transitions]
        assert "action_confirm" in actions
        assert "action_cancel" in actions

    def test_sale_transitions(self) -> None:
        wf = get_sale_order_workflow()
        sale = wf.states["sale"]
        actions = [t.action for t in sale.transitions]
        assert "action_done" in actions
        assert "action_cancel" in actions

    def test_cancel_can_reset_to_draft(self) -> None:
        wf = get_sale_order_workflow()
        cancel = wf.states["cancel"]
        actions = [t.action for t in cancel.transitions]
        assert "action_draft" in actions

    def test_done_has_no_transitions(self) -> None:
        wf = get_sale_order_workflow()
        done = wf.states["done"]
        assert done.transitions == ()

    def test_has_create_guide(self) -> None:
        wf = get_sale_order_workflow()
        assert wf.create_guide is not None
        assert wf.create_guide.line_model == "sale.order.line"
        assert wf.create_guide.line_field == "order_line"

    def test_create_guide_has_partner_hint(self) -> None:
        wf = get_sale_order_workflow()
        assert wf.create_guide is not None
        partner_hints = [
            r
            for r in wf.create_guide.required_relations
            if r.field_name == "partner_id"
        ]
        assert len(partner_hints) == 1
        assert partner_hints[0].relation_model == "res.partner"
        assert partner_hints[0].required is True

    def test_has_version_notes(self) -> None:
        wf = get_sale_order_workflow()
        assert "19" in wf.version_notes

    def test_all_target_states_are_defined(self) -> None:
        wf = get_sale_order_workflow()
        targets = _get_all_target_states(wf)
        assert targets.issubset(set(wf.states.keys()))

    def test_all_transitions_have_labels(self) -> None:
        wf = get_sale_order_workflow()
        for state_def in wf.states.values():
            for t in state_def.transitions:
                assert t.label, f"Transition {t.action} has no label"

    def test_state_labels_are_nonempty(self) -> None:
        wf = get_sale_order_workflow()
        for state_def in wf.states.values():
            assert state_def.label, f"State {state_def.name} has no label"


# ------------------------------------------------------------------
# purchase.order
# ------------------------------------------------------------------


class TestPurchaseOrderWorkflow:
    def test_model(self) -> None:
        wf = get_purchase_order_workflow()
        assert wf.model == "purchase.order"
        assert wf.display_name == "Purchase Order"
        assert wf.state_field == "state"

    def test_has_expected_states(self) -> None:
        wf = get_purchase_order_workflow()
        expected = {"draft", "sent", "purchase", "done", "cancel"}
        assert set(wf.states.keys()) == expected

    def test_draft_transitions(self) -> None:
        wf = get_purchase_order_workflow()
        draft = wf.states["draft"]
        actions = [t.action for t in draft.transitions]
        assert "button_confirm" in actions
        assert "button_cancel" in actions

    def test_purchase_transitions(self) -> None:
        wf = get_purchase_order_workflow()
        purchase = wf.states["purchase"]
        actions = [t.action for t in purchase.transitions]
        assert "button_done" in actions
        assert "button_cancel" in actions

    def test_cancel_can_reset_to_draft(self) -> None:
        wf = get_purchase_order_workflow()
        cancel = wf.states["cancel"]
        actions = [t.action for t in cancel.transitions]
        assert "button_draft" in actions

    def test_has_create_guide(self) -> None:
        wf = get_purchase_order_workflow()
        assert wf.create_guide is not None
        assert wf.create_guide.line_model == "purchase.order.line"

    def test_create_guide_has_partner_hint(self) -> None:
        wf = get_purchase_order_workflow()
        assert wf.create_guide is not None
        partner_hints = [
            r
            for r in wf.create_guide.required_relations
            if r.field_name == "partner_id"
        ]
        assert len(partner_hints) == 1
        assert "vendor" in partner_hints[0].hint.lower()

    def test_all_target_states_are_defined(self) -> None:
        wf = get_purchase_order_workflow()
        targets = _get_all_target_states(wf)
        assert targets.issubset(set(wf.states.keys()))


# ------------------------------------------------------------------
# hr.leave
# ------------------------------------------------------------------


class TestHrLeaveWorkflow:
    def test_model(self) -> None:
        wf = get_hr_leave_workflow()
        assert wf.model == "hr.leave"
        assert wf.display_name == "Time Off Request"
        assert wf.state_field == "state"

    def test_has_expected_states(self) -> None:
        wf = get_hr_leave_workflow()
        expected = {"draft", "confirm", "validate1", "validate", "refuse"}
        assert set(wf.states.keys()) == expected

    def test_draft_transitions(self) -> None:
        wf = get_hr_leave_workflow()
        draft = wf.states["draft"]
        actions = [t.action for t in draft.transitions]
        assert "action_confirm" in actions

    def test_confirm_transitions(self) -> None:
        wf = get_hr_leave_workflow()
        confirm = wf.states["confirm"]
        actions = [t.action for t in confirm.transitions]
        assert "action_approve" in actions
        assert "action_refuse" in actions
        assert "action_draft" in actions

    def test_validate1_transitions(self) -> None:
        wf = get_hr_leave_workflow()
        v1 = wf.states["validate1"]
        actions = [t.action for t in v1.transitions]
        assert "action_validate" in actions
        assert "action_refuse" in actions

    def test_refuse_can_reset_to_draft(self) -> None:
        wf = get_hr_leave_workflow()
        refuse = wf.states["refuse"]
        actions = [t.action for t in refuse.transitions]
        assert "action_draft" in actions

    def test_has_create_guide(self) -> None:
        wf = get_hr_leave_workflow()
        assert wf.create_guide is not None
        fields = [r.field_name for r in wf.create_guide.required_relations]
        assert "holiday_status_id" in fields
        assert "employee_id" in fields

    def test_all_target_states_are_defined(self) -> None:
        wf = get_hr_leave_workflow()
        targets = _get_all_target_states(wf)
        assert targets.issubset(set(wf.states.keys()))


# ------------------------------------------------------------------
# helpdesk.ticket
# ------------------------------------------------------------------


class TestHelpdeskTicketWorkflow:
    def test_model(self) -> None:
        wf = get_helpdesk_ticket_workflow()
        assert wf.model == "helpdesk.ticket"
        assert wf.display_name == "Helpdesk Ticket"
        assert wf.state_field == "stage_id"

    def test_has_states(self) -> None:
        wf = get_helpdesk_ticket_workflow()
        assert len(wf.states) >= 3

    def test_stage_based(self) -> None:
        """Helpdesk uses stage_id, not state."""
        wf = get_helpdesk_ticket_workflow()
        assert wf.state_field == "stage_id"

    def test_has_create_guide(self) -> None:
        wf = get_helpdesk_ticket_workflow()
        assert wf.create_guide is not None
        fields = [r.field_name for r in wf.create_guide.required_relations]
        assert "team_id" in fields

    def test_create_guide_mentions_stages(self) -> None:
        wf = get_helpdesk_ticket_workflow()
        assert wf.create_guide is not None
        assert "stage" in wf.create_guide.notes.lower()

    def test_has_recommended_fields(self) -> None:
        wf = get_helpdesk_ticket_workflow()
        assert wf.create_guide is not None
        assert "name" in wf.create_guide.recommended_fields

    def test_partner_is_optional(self) -> None:
        wf = get_helpdesk_ticket_workflow()
        assert wf.create_guide is not None
        partner_hints = [
            r
            for r in wf.create_guide.required_relations
            if r.field_name == "partner_id"
        ]
        assert len(partner_hints) == 1
        assert partner_hints[0].required is False


# ------------------------------------------------------------------
# crm.lead
# ------------------------------------------------------------------


class TestCrmLeadWorkflow:
    def test_model(self) -> None:
        wf = get_crm_lead_workflow()
        assert wf.model == "crm.lead"
        assert wf.display_name == "CRM Lead / Opportunity"
        assert wf.state_field == "stage_id"

    def test_has_states(self) -> None:
        wf = get_crm_lead_workflow()
        assert len(wf.states) >= 3

    def test_stage_based(self) -> None:
        wf = get_crm_lead_workflow()
        assert wf.state_field == "stage_id"

    def test_has_key_actions(self) -> None:
        wf = get_crm_lead_workflow()
        all_actions = _get_all_transitions(wf)
        assert "action_set_won" in all_actions
        assert "action_set_lost" in all_actions
        assert "convert_opportunity" in all_actions

    def test_has_create_guide(self) -> None:
        wf = get_crm_lead_workflow()
        assert wf.create_guide is not None
        assert "name" in wf.create_guide.recommended_fields
        assert "type" in wf.create_guide.recommended_fields

    def test_has_version_notes(self) -> None:
        wf = get_crm_lead_workflow()
        assert "19" in wf.version_notes

    def test_partner_is_optional(self) -> None:
        wf = get_crm_lead_workflow()
        assert wf.create_guide is not None
        partner_hints = [
            r
            for r in wf.create_guide.required_relations
            if r.field_name == "partner_id"
        ]
        assert len(partner_hints) == 1
        assert partner_hints[0].required is False

    def test_won_has_no_transitions(self) -> None:
        wf = get_crm_lead_workflow()
        won = wf.states["won"]
        assert won.transitions == ()


# ------------------------------------------------------------------
# Cross-workflow validation
# ------------------------------------------------------------------


class TestAllWorkflowIntegrity:
    @pytest.fixture()
    def all_workflows(self) -> list[WorkflowDef]:
        return get_all_stock_workflows()

    def test_all_have_model(self, all_workflows: list[WorkflowDef]) -> None:
        for wf in all_workflows:
            assert wf.model, "Workflow has no model"

    def test_all_have_display_name(self, all_workflows: list[WorkflowDef]) -> None:
        for wf in all_workflows:
            assert wf.display_name, f"{wf.model} has no display_name"

    def test_all_have_state_field(self, all_workflows: list[WorkflowDef]) -> None:
        for wf in all_workflows:
            assert wf.state_field, f"{wf.model} has no state_field"

    def test_all_have_at_least_two_states(
        self, all_workflows: list[WorkflowDef]
    ) -> None:
        for wf in all_workflows:
            assert len(wf.states) >= 2, f"{wf.model} has fewer than 2 states"

    def test_all_have_create_guide(
        self, all_workflows: list[WorkflowDef]
    ) -> None:
        for wf in all_workflows:
            assert wf.create_guide is not None, f"{wf.model} has no create_guide"

    def test_no_empty_labels(self, all_workflows: list[WorkflowDef]) -> None:
        for wf in all_workflows:
            for state_def in wf.states.values():
                assert state_def.label, (
                    f"{wf.model}.{state_def.name} has no label"
                )
                for t in state_def.transitions:
                    assert t.label, (
                        f"{wf.model}.{state_def.name}->{t.action} has no label"
                    )

    def test_no_empty_actions(self, all_workflows: list[WorkflowDef]) -> None:
        for wf in all_workflows:
            for state_def in wf.states.values():
                for t in state_def.transitions:
                    assert t.action, (
                        f"{wf.model}.{state_def.name} has transition with "
                        "no action"
                    )

    def test_no_empty_target_states(
        self, all_workflows: list[WorkflowDef]
    ) -> None:
        for wf in all_workflows:
            for state_def in wf.states.values():
                for t in state_def.transitions:
                    assert t.target_state, (
                        f"{wf.model}.{state_def.name}->{t.action} has no "
                        "target_state"
                    )
