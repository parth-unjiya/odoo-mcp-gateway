"""Tests for field name validation functions in crud.py.

Covers _validate_fields (regular field names) and _validate_agg_fields
(aggregate field names with optional :operator suffix).
"""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.tools.crud import _validate_agg_fields, _validate_fields


class TestValidateFields:
    """Tests for the _validate_fields function."""

    # ── Valid field names ─────────────────────────────────────────

    def test_valid_simple_fields(self) -> None:
        result = _validate_fields(["name", "email", "phone"])
        assert result == ["name", "email", "phone"]

    def test_valid_relational_field(self) -> None:
        result = _validate_fields(["partner_id"])
        assert result == ["partner_id"]

    def test_valid_amount_field(self) -> None:
        result = _validate_fields(["amount_total"])
        assert result == ["amount_total"]

    def test_valid_dotted_field(self) -> None:
        """Dotted notation for related fields (e.g. partner_id.name)."""
        result = _validate_fields(["partner_id.name"])
        assert result == ["partner_id.name"]

    def test_valid_multi_dot_field(self) -> None:
        result = _validate_fields(["order_id.partner_id.name"])
        assert result == ["order_id.partner_id.name"]

    def test_empty_list_returns_empty(self) -> None:
        result = _validate_fields([])
        assert result == []

    # ── Invalid field names ──────────────────────────────────────

    def test_rejects_sql_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["name; DROP TABLE"])

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["123bad"])

    def test_rejects_empty_string_field(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields([""])

    def test_rejects_star(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["*"])

    def test_rejects_double_dash(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["name--x"])

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["Name"])

    def test_rejects_space(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["na me"])

    def test_rejects_special_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["name$"])

    def test_rejects_hyphen(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["first-name"])

    def test_rejects_leading_underscore(self) -> None:
        """Field names must start with a lowercase letter per _FIELD_NAME_RE."""
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["_private"])

    def test_one_bad_field_in_list_raises(self) -> None:
        """Even if most fields are valid, one bad one should raise."""
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["name", "email", "'; DROP TABLE", "phone"])

    def test_rejects_leading_dot(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields([".name"])

    def test_rejects_trailing_dot(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_fields(["name."])


class TestValidateAggFields:
    """Tests for the _validate_agg_fields function (read_group aggregate fields)."""

    # ── Valid aggregate field names ───────────────────────────────

    def test_valid_plain_field(self) -> None:
        result = _validate_agg_fields(["amount_total"])
        assert result == ["amount_total"]

    def test_valid_with_sum_operator(self) -> None:
        result = _validate_agg_fields(["amount_total:sum"])
        assert result == ["amount_total:sum"]

    def test_valid_with_avg_operator(self) -> None:
        result = _validate_agg_fields(["count:avg"])
        assert result == ["count:avg"]

    def test_valid_multiple(self) -> None:
        result = _validate_agg_fields(["amount_total:sum", "qty:count"])
        assert result == ["amount_total:sum", "qty:count"]

    def test_empty_list(self) -> None:
        result = _validate_agg_fields([])
        assert result == []

    # ── Invalid aggregate field names ────────────────────────────

    def test_rejects_sql_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields(["name; DROP TABLE:sum"])

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields([""])

    def test_rejects_star(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields(["*"])

    def test_rejects_dotted_field(self) -> None:
        """Aggregate fields do not support dotted notation."""
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields(["partner_id.name:sum"])

    def test_rejects_double_colon(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields(["amount::sum"])

    def test_rejects_uppercase_operator(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields(["amount:SUM"])

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(ValueError, match="Invalid field name"):
            _validate_agg_fields(["1field:sum"])
