"""Tests for domain building and validation."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from odoo_mcp_gateway.utils.domain_builder import (
    MAX_DOMAIN_DEPTH,
    MAX_DOMAIN_LEAVES,
    MAX_FIELD_TRAVERSAL,
    DomainValidationError,
    validate_domain,
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateDomain:
    """Tests for validate_domain."""

    def test_validate_empty_domain(self) -> None:
        assert validate_domain([]) == []

    def test_validate_simple_domain(self) -> None:
        domain = [("state", "=", "draft")]
        assert validate_domain(domain) == domain

    def test_validate_or_domain(self) -> None:
        domain = ["|", ("state", "=", "draft"), ("state", "=", "sent")]
        assert validate_domain(domain) == domain

    def test_validate_and_domain(self) -> None:
        domain = ["&", ("state", "=", "draft"), ("amount", ">", 100)]
        assert validate_domain(domain) == domain

    def test_validate_nested_domain(self) -> None:
        domain = [
            "|",
            "&",
            ("state", "=", "draft"),
            ("amount", ">", 100),
            ("state", "=", "done"),
        ]
        assert validate_domain(domain) == domain

    def test_validate_invalid_operator(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid operator"):
            validate_domain([("name", "LIKE", "test")])

    def test_validate_invalid_field_name(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid field name"):
            validate_domain([("Name", "=", "test")])

    def test_validate_sql_injection_field(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid field name"):
            validate_domain([("name; DROP TABLE--", "=", "x")])

    def test_validate_field_traversal(self) -> None:
        domain = [("partner_id.country_id.code", "=", "US")]
        assert validate_domain(domain) == domain

    def test_validate_field_traversal_too_deep(self) -> None:
        deep_field = ".".join(["a"] * (MAX_FIELD_TRAVERSAL + 1))
        with pytest.raises(DomainValidationError, match="Field traversal too deep"):
            validate_domain([(deep_field, "=", "x")])

    def test_validate_non_list_domain(self) -> None:
        with pytest.raises(DomainValidationError, match="must be a list"):
            validate_domain("not a list")  # type: ignore[arg-type]

    def test_validate_bad_leaf_length(self) -> None:
        with pytest.raises(DomainValidationError, match="must have 3 elements"):
            validate_domain([("name", "=")])

    def test_validate_too_many_leaves(self) -> None:
        domain = [("field_a", "=", i) for i in range(MAX_DOMAIN_LEAVES + 1)]
        with pytest.raises(DomainValidationError, match="Too many domain conditions"):
            validate_domain(domain)

    def test_validate_too_deeply_nested(self) -> None:
        # Create a deeply nested domain with boolean operators
        domain: list = []
        for _ in range(MAX_DOMAIN_DEPTH + 1):
            domain.append("&")
        # Add enough leaves for the boolean operators
        for i in range(MAX_DOMAIN_DEPTH + 2):
            domain.append(("field_a", "=", i))
        with pytest.raises(DomainValidationError, match="too deeply nested"):
            validate_domain(domain)

    def test_validate_invalid_boolean_op(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid boolean operator"):
            validate_domain(["AND", ("name", "=", "test")])

    def test_validate_value_types(self) -> None:
        # Strings, ints, floats, bools should all be valid
        validate_domain([("name", "=", "text")])
        validate_domain([("amount", "=", 42)])
        validate_domain([("rate", "=", 3.14)])
        validate_domain([("active", "=", True)])

    def test_validate_value_list(self) -> None:
        domain = [("id", "in", [1, 2, 3])]
        assert validate_domain(domain) == domain

    def test_validate_value_none(self) -> None:
        domain = [("parent_id", "=", None)]
        assert validate_domain(domain) == domain

    def test_validate_invalid_value_type(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid value type"):
            validate_domain([("name", "=", object())])

    def test_validate_invalid_element_type(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid domain element type"):
            validate_domain([42])  # type: ignore[list-item]

    def test_validate_date_value(self) -> None:
        domain = [("date_order", ">=", date(2026, 1, 1))]
        assert validate_domain(domain) == domain

    def test_validate_datetime_value(self) -> None:
        domain = [("create_date", "<=", datetime(2026, 12, 31, 23, 59, 59))]
        assert validate_domain(domain) == domain

    def test_validate_field_non_string(self) -> None:
        with pytest.raises(DomainValidationError, match="Field must be a string"):
            validate_domain([(123, "=", "x")])

    def test_validate_operator_non_string(self) -> None:
        with pytest.raises(DomainValidationError, match="Operator must be a string"):
            validate_domain([("name", 42, "x")])

    def test_validate_value_list_with_invalid_item(self) -> None:
        with pytest.raises(DomainValidationError, match="Invalid value in list"):
            validate_domain([("id", "in", [1, object()])])

    def test_validate_not_operator(self) -> None:
        domain = ["!", ("active", "=", False)]
        assert validate_domain(domain) == domain

    def test_validate_all_operators(self) -> None:
        """Every valid operator should pass validation."""
        from odoo_mcp_gateway.utils.domain_builder import VALID_OPERATORS

        for op in VALID_OPERATORS:
            if op in ("in", "not in"):
                validate_domain([("field_a", op, [1])])
            else:
                validate_domain([("field_a", op, "val")])
