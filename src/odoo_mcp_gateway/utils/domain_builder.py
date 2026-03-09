"""Odoo domain filter construction and validation."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# Valid Odoo domain operators
VALID_OPERATORS = frozenset(
    {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "like",
        "not like",
        "ilike",
        "not ilike",
        "=like",
        "=ilike",
        "in",
        "not in",
        "child_of",
        "parent_of",
        "=?",
    }
)

# Maximum domain depth (nested AND/OR)
MAX_DOMAIN_DEPTH = 10

# Maximum number of leaf conditions
MAX_DOMAIN_LEAVES = 50

# Pattern for valid field paths (supports dotted traversal)
_FIELD_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

# Maximum traversal depth for dotted field paths
MAX_FIELD_TRAVERSAL = 4

# Maximum items in an "in" / "not in" value list
MAX_IN_LIST_SIZE = 10_000


class DomainValidationError(ValueError):
    """Raised when a domain filter is invalid or unsafe."""


def validate_domain(domain: list[Any]) -> list[Any]:  # noqa: C901
    """Validate and sanitize an Odoo domain filter.

    Checks:
    - Domain is a list
    - Each leaf is a 3-tuple (field, operator, value)
    - Operators are valid Odoo operators
    - Field names are valid (no SQL injection)
    - Domain depth is within limits
    - No excessive leaves

    Returns the validated domain (pass-through if valid).
    Raises DomainValidationError if invalid.
    """
    if not isinstance(domain, list):
        raise DomainValidationError("Domain must be a list")

    if not domain:
        return domain

    leaf_count = 0
    depth = 0
    max_depth = 0

    for item in domain:
        if isinstance(item, str):
            # Boolean operator: & | !
            if item not in ("&", "|", "!"):
                raise DomainValidationError(
                    f"Invalid boolean operator: '{item}'. Must be '&', '|', or '!'"
                )
            depth += 1
            max_depth = max(max_depth, depth)
            if max_depth > MAX_DOMAIN_DEPTH:
                raise DomainValidationError(
                    f"Domain too deeply nested (max {MAX_DOMAIN_DEPTH})"
                )
        elif isinstance(item, (list, tuple)):
            if len(item) != 3:
                raise DomainValidationError(
                    "Domain leaf must have 3 elements (field, op, value),"
                    f" got {len(item)}"
                )
            field, op, value = item
            _validate_field_path(field)
            _validate_operator(op)
            _validate_value(value)
            leaf_count += 1
            if leaf_count > MAX_DOMAIN_LEAVES:
                raise DomainValidationError(
                    f"Too many domain conditions (max {MAX_DOMAIN_LEAVES})"
                )
            if depth > 0:
                depth -= 1
        else:
            raise DomainValidationError(
                f"Invalid domain element type: {type(item).__name__}"
            )

    return domain


def _validate_field_path(field: Any) -> None:
    """Validate a field path like 'partner_id.country_id.code'."""
    if not isinstance(field, str):
        raise DomainValidationError(
            f"Field must be a string, got {type(field).__name__}"
        )
    if not _FIELD_PATH_RE.match(field):
        raise DomainValidationError(f"Invalid field name: '{field}'")
    parts = field.split(".")
    if len(parts) > MAX_FIELD_TRAVERSAL:
        raise DomainValidationError(
            f"Field traversal too deep: '{field}'"
            f" ({len(parts)} levels, max {MAX_FIELD_TRAVERSAL})"
        )


def _validate_operator(op: Any) -> None:
    """Validate a domain operator."""
    if not isinstance(op, str):
        raise DomainValidationError(
            f"Operator must be a string, got {type(op).__name__}"
        )
    if op not in VALID_OPERATORS:
        raise DomainValidationError(f"Invalid operator: '{op}'")


def _validate_value(value: Any) -> None:
    """Validate a domain filter value."""
    # Allow: str, int, float, bool, None, list, date, datetime
    if value is None:
        return
    if isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, (date, datetime)):
        return
    if isinstance(value, (list, tuple)):
        # For "in" / "not in" operators -- validate each element
        if len(value) > MAX_IN_LIST_SIZE:
            raise DomainValidationError(
                f"Value list too long ({len(value)} items, max {MAX_IN_LIST_SIZE})"
            )
        for item in value:
            if not isinstance(item, (str, int, float, bool, type(None))):
                raise DomainValidationError(
                    f"Invalid value in list: {type(item).__name__}"
                )
        return
    raise DomainValidationError(f"Invalid value type: {type(value).__name__}")
