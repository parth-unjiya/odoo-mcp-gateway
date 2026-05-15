"""Tests for workflow definition dataclasses."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.workflow.definitions import (
    CreateGuide,
    RelationHint,
    StateDef,
    TransitionDef,
    WorkflowDef,
)

# ------------------------------------------------------------------
# TransitionDef
# ------------------------------------------------------------------


class TestTransitionDef:
    def test_create_with_all_fields(self) -> None:
        t = TransitionDef(
            action="action_confirm",
            target_state="sale",
            label="Confirm Order",
            description="Confirms the quotation.",
        )
        assert t.action == "action_confirm"
        assert t.target_state == "sale"
        assert t.label == "Confirm Order"
        assert t.description == "Confirms the quotation."

    def test_default_description_is_empty(self) -> None:
        t = TransitionDef(
            action="action_cancel",
            target_state="cancel",
            label="Cancel",
        )
        assert t.description == ""

    def test_frozen_raises_on_mutation(self) -> None:
        t = TransitionDef(
            action="action_confirm",
            target_state="sale",
            label="Confirm",
        )
        with pytest.raises(AttributeError):
            t.action = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        t1 = TransitionDef(action="a", target_state="b", label="c")
        t2 = TransitionDef(action="a", target_state="b", label="c")
        assert t1 == t2

    def test_hashable(self) -> None:
        t = TransitionDef(action="a", target_state="b", label="c")
        assert hash(t) is not None
        # Can be used in a set
        s = {t}
        assert t in s


# ------------------------------------------------------------------
# StateDef
# ------------------------------------------------------------------


class TestStateDef:
    def test_create_with_transitions(self) -> None:
        t = TransitionDef(action="act", target_state="next", label="Go")
        s = StateDef(name="draft", label="Draft", transitions=(t,))
        assert s.name == "draft"
        assert s.label == "Draft"
        assert len(s.transitions) == 1
        assert s.transitions[0].action == "act"

    def test_default_transitions_is_empty(self) -> None:
        s = StateDef(name="done", label="Done")
        assert s.transitions == ()

    def test_frozen_raises_on_mutation(self) -> None:
        s = StateDef(name="draft", label="Draft")
        with pytest.raises(AttributeError):
            s.name = "other"  # type: ignore[misc]

    def test_multiple_transitions(self) -> None:
        transitions = (
            TransitionDef(action="confirm", target_state="sale", label="Confirm"),
            TransitionDef(action="cancel", target_state="cancel", label="Cancel"),
        )
        s = StateDef(name="draft", label="Draft", transitions=transitions)
        assert len(s.transitions) == 2
        assert s.transitions[0].action == "confirm"
        assert s.transitions[1].action == "cancel"


# ------------------------------------------------------------------
# RelationHint
# ------------------------------------------------------------------


class TestRelationHint:
    def test_create_required(self) -> None:
        r = RelationHint(
            field_name="partner_id",
            relation_model="res.partner",
            hint="Search for customer",
            required=True,
        )
        assert r.field_name == "partner_id"
        assert r.relation_model == "res.partner"
        assert r.hint == "Search for customer"
        assert r.required is True

    def test_default_required_is_true(self) -> None:
        r = RelationHint(
            field_name="partner_id",
            relation_model="res.partner",
            hint="hint",
        )
        assert r.required is True

    def test_optional_relation(self) -> None:
        r = RelationHint(
            field_name="partner_id",
            relation_model="res.partner",
            hint="hint",
            required=False,
        )
        assert r.required is False

    def test_frozen(self) -> None:
        r = RelationHint(field_name="f", relation_model="m", hint="h")
        with pytest.raises(AttributeError):
            r.field_name = "other"  # type: ignore[misc]


# ------------------------------------------------------------------
# CreateGuide
# ------------------------------------------------------------------


class TestCreateGuide:
    def test_defaults(self) -> None:
        g = CreateGuide()
        assert g.required_relations == ()
        assert g.recommended_fields == ()
        assert g.line_model is None
        assert g.line_field is None
        assert g.notes == ""

    def test_with_all_fields(self) -> None:
        r = RelationHint(
            field_name="partner_id",
            relation_model="res.partner",
            hint="hint",
        )
        g = CreateGuide(
            required_relations=(r,),
            recommended_fields=("date_order", "user_id"),
            line_model="sale.order.line",
            line_field="order_line",
            notes="Add lines after creation.",
        )
        assert len(g.required_relations) == 1
        assert g.recommended_fields == ("date_order", "user_id")
        assert g.line_model == "sale.order.line"
        assert g.line_field == "order_line"
        assert g.notes == "Add lines after creation."

    def test_frozen(self) -> None:
        g = CreateGuide()
        with pytest.raises(AttributeError):
            g.notes = "changed"  # type: ignore[misc]


# ------------------------------------------------------------------
# WorkflowDef
# ------------------------------------------------------------------


class TestWorkflowDef:
    def test_create_minimal(self) -> None:
        wf = WorkflowDef(
            model="sale.order",
            display_name="Sales Order",
            state_field="state",
            states={},
        )
        assert wf.model == "sale.order"
        assert wf.display_name == "Sales Order"
        assert wf.state_field == "state"
        assert wf.states == {}
        assert wf.create_guide is None
        assert wf.version_notes == {}

    def test_create_with_states_and_guide(self) -> None:
        s = StateDef(name="draft", label="Draft")
        g = CreateGuide(notes="Some notes")
        wf = WorkflowDef(
            model="sale.order",
            display_name="Sales Order",
            state_field="state",
            states={"draft": s},
            create_guide=g,
            version_notes={"19": "tax_id renamed"},
        )
        assert "draft" in wf.states
        assert wf.create_guide is not None
        assert wf.create_guide.notes == "Some notes"
        assert wf.version_notes["19"] == "tax_id renamed"

    def test_frozen(self) -> None:
        wf = WorkflowDef(
            model="sale.order",
            display_name="Sales Order",
            state_field="state",
            states={},
        )
        with pytest.raises(AttributeError):
            wf.model = "other"  # type: ignore[misc]

    def test_version_notes_default_factory(self) -> None:
        """Each instance should get its own dict."""
        wf1 = WorkflowDef(model="a", display_name="A", state_field="s", states={})
        wf2 = WorkflowDef(model="b", display_name="B", state_field="s", states={})
        assert wf1.version_notes is not wf2.version_notes

    def test_states_can_have_nested_transitions(self) -> None:
        t1 = TransitionDef(action="confirm", target_state="sale", label="Confirm")
        t2 = TransitionDef(action="cancel", target_state="cancel", label="Cancel")
        s = StateDef(name="draft", label="Draft", transitions=(t1, t2))
        wf = WorkflowDef(
            model="sale.order",
            display_name="SO",
            state_field="state",
            states={"draft": s},
        )
        assert len(wf.states["draft"].transitions) == 2
