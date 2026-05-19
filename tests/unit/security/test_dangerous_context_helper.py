"""Regression tests for the lifted DANGEROUS_CONTEXT_KEYS helper (H5).

The constant + filter helper used to live in ``tools/crud.py`` where
only ``execute_method`` consumed them. New plugin tools or future CRUD
tools that accept a ``context`` kwarg can now import the canonical
helpers from ``core.security`` so the filter behaviour is uniform.
"""

from __future__ import annotations

from odoo_mcp_gateway.core.security import (
    DANGEROUS_CONTEXT_KEYS,
    filter_dangerous_context_keys,
)


class TestDangerousContextKeys:
    def test_set_includes_all_known_bypass_primitives(self) -> None:
        # Every key in this list has a real Odoo-side bypass effect;
        # the helper MUST keep filtering them. Adding more is fine;
        # removing is a regression.
        assert "active_test" in DANGEROUS_CONTEXT_KEYS
        assert "tracking_disable" in DANGEROUS_CONTEXT_KEYS
        assert "force_company" in DANGEROUS_CONTEXT_KEYS
        assert "allowed_company_ids" in DANGEROUS_CONTEXT_KEYS
        assert "mail_create_nolog" in DANGEROUS_CONTEXT_KEYS
        assert "mail_create_nosubscribe" in DANGEROUS_CONTEXT_KEYS
        assert "mail_notrack" in DANGEROUS_CONTEXT_KEYS
        assert "default_company_id" in DANGEROUS_CONTEXT_KEYS
        assert "no_reset_password" in DANGEROUS_CONTEXT_KEYS
        assert "check_move_validity" in DANGEROUS_CONTEXT_KEYS

    def test_filter_strips_dangerous_keys(self) -> None:
        ctx = {
            "lang": "en_US",
            "tz": "UTC",
            "tracking_disable": True,
            "force_company": 1,
        }
        safe, stripped = filter_dangerous_context_keys(ctx)
        assert safe == {"lang": "en_US", "tz": "UTC"}
        assert sorted(stripped) == ["force_company", "tracking_disable"]

    def test_filter_does_not_mutate_input(self) -> None:
        ctx = {"tracking_disable": True, "lang": "fr"}
        original = dict(ctx)
        _ = filter_dangerous_context_keys(ctx)
        assert ctx == original  # input untouched

    def test_filter_none_returns_empty(self) -> None:
        safe, stripped = filter_dangerous_context_keys(None)
        assert safe == {}
        assert stripped == []

    def test_filter_non_dict_returns_empty(self) -> None:
        # Defensive: an attacker-controlled ``context`` field may not
        # be a dict. We coerce to empty rather than raising.
        safe, stripped = filter_dangerous_context_keys("not a dict")  # type: ignore[arg-type]
        assert safe == {}
        assert stripped == []

    def test_legacy_crud_alias_still_works(self) -> None:
        # Backwards compat for tests that imported the private symbol.
        from odoo_mcp_gateway.tools.crud import _DANGEROUS_CONTEXT_KEYS

        assert _DANGEROUS_CONTEXT_KEYS is DANGEROUS_CONTEXT_KEYS
