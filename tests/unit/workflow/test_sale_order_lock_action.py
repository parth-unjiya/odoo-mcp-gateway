"""Tests for the sale.order action_lock / action_done dual-listing (P2-3).

In Odoo 19 ``sale.order.action_done`` was removed in favour of
``action_lock``. Rather than version-gating the workflow definition we
list both as alternative transitions out of the ``sale`` state — analogous
to how hr.leave handles v17/v18 differences. The test suite below pins
the contract so a future refactor doesn't accidentally drop one of them.
"""

from __future__ import annotations

from odoo_mcp_gateway.core.workflow.stock_workflows.sale_order import (
    get_workflow,
)


class TestSaleOrderLockAlternatives:
    def test_both_lock_methods_present_on_sale_state(self) -> None:
        wf = get_workflow()
        sale = wf.states["sale"]
        actions = [t.action for t in sale.transitions]

        # action_lock is the v18+/v19 method name.
        assert "action_lock" in actions
        # action_done is preserved for v17 compatibility.
        assert "action_done" in actions

    def test_lock_methods_both_target_done(self) -> None:
        """Both transitions lead to the same target state so callers
        don't end up in different end states depending on which method
        they invoke.
        """
        wf = get_workflow()
        sale = wf.states["sale"]
        lock_transitions = [
            t for t in sale.transitions if t.action in {"action_lock", "action_done"}
        ]

        assert len(lock_transitions) == 2
        for t in lock_transitions:
            assert t.target_state == "done"

    def test_action_lock_listed_before_action_done(self) -> None:
        """Preferred method (action_lock for v18+/v19) appears first so
        callers iterating in order pick the modern variant.
        """
        wf = get_workflow()
        sale = wf.states["sale"]
        actions = [t.action for t in sale.transitions]

        lock_idx = actions.index("action_lock")
        done_idx = actions.index("action_done")
        assert lock_idx < done_idx

    def test_action_done_labeled_as_legacy(self) -> None:
        """The action_done variant must advertise itself as the legacy
        path so AI agents prefer action_lock by default.
        """
        wf = get_workflow()
        sale = wf.states["sale"]
        done = next(t for t in sale.transitions if t.action == "action_done")

        # Label or description should contain a v17 hint.
        marker = (done.label + " " + done.description).lower()
        assert "v17" in marker or "legacy" in marker

    def test_version_notes_describe_per_version_method(self) -> None:
        """Documentation of the renames so callers know which path to use."""
        wf = get_workflow()
        notes = wf.version_notes

        # Each supported major version is documented.
        assert "17" in notes
        assert "18" in notes
        assert "19" in notes
        # Mentions both names somewhere in the notes.
        all_notes = " ".join(notes.values()).lower()
        assert "action_lock" in all_notes
        assert "action_done" in all_notes
