"""Regression tests for validator tightening (M-tools-1).

Covers:
* ``_AGG_FIELD_RE`` aggregate suffix closed allowlist (rejects garbage
  suffixes like ``:rmrf``).
* ``_validate_order`` rejects the ``field,direction`` comma typo.
* ``_validate_order`` enforces the centralised
  ``MAX_FIELD_TRAVERSAL`` depth.
* ``_MODEL_PATTERN`` rejects trailing-dot model names.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from odoo_mcp_gateway.core.security.config_loader import RestrictionConfig
from odoo_mcp_gateway.tools.crud import (
    _AGG_FIELD_RE,
    _validate_agg_fields,
    _validate_order,
)


class TestAggSuffixAllowlist:
    @pytest.mark.parametrize(
        "suffix",
        [
            "sum",
            "avg",
            "min",
            "max",
            "count",
            "count_distinct",
            "array_agg",
            "bool_and",
            "bool_or",
        ],
    )
    def test_allowed_suffixes(self, suffix: str) -> None:
        assert _AGG_FIELD_RE.match(f"amount_total:{suffix}")

    @pytest.mark.parametrize("bad", ["rmrf", "drop", "exec", "del", "fooo"])
    def test_unknown_suffix_rejected(self, bad: str) -> None:
        assert not _AGG_FIELD_RE.match(f"amount_total:{bad}")

    def test_validate_agg_fields_raises_on_unknown_suffix(self) -> None:
        with pytest.raises(ValueError):
            _validate_agg_fields(["amount_total:rmrf"])


class TestValidateOrder:
    def test_field_comma_direction_typo_rejected(self) -> None:
        """The typo 'create_date,desc' must NOT be silently accepted."""
        with pytest.raises(ValueError, match="direction must follow a field"):
            _validate_order("create_date,desc")

    def test_normal_two_field_order_accepted(self) -> None:
        assert _validate_order("create_date desc, id asc") == (
            "create_date desc, id asc"
        )

    def test_deep_traversal_rejected(self) -> None:
        with pytest.raises(ValueError, match="too deep"):
            # 5 segments (4 dots) exceeds MAX_FIELD_TRAVERSAL=4.
            _validate_order("a.b.c.d.e desc")

    def test_4_segment_order_accepted(self) -> None:
        # Exactly MAX_FIELD_TRAVERSAL=4 → 3 dots, 4 segments.
        # The cap was previously '> 3' (count of dots) which matched
        # 4 segments by accident. Now centralised on segment count.
        assert _validate_order("a.b.c.d asc") == "a.b.c.d asc"


class TestModelPatternStrict:
    def test_trailing_dot_rejected(self) -> None:
        # An invalid model name must raise (pydantic wraps the ValueError
        # raised inside the field_validator into a ValidationError).
        with pytest.raises(ValidationError, match="Invalid model name"):
            RestrictionConfig(always_blocked=["res.partner."])

    def test_empty_segment_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid model name"):
            RestrictionConfig(always_blocked=["res..partner"])

    def test_canonical_name_accepted(self) -> None:
        cfg = RestrictionConfig(always_blocked=["res.partner", "ir.attachment"])
        assert "res.partner" in cfg.always_blocked
