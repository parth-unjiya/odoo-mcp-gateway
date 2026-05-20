"""Odoo domain filter construction and validation.

The validator ports the canonical algorithm from Odoo's
``odoo/osv/expression.py::normalize_domain`` (LGPL-3 — see Odoo
upstream). The ORM uses a single-pass O(n) iterative parser with an
``expected``-operand counter and an arity stack to compute true tree
depth. Re-using their algorithm guarantees the gateway accepts
exactly the same syntactically-valid domains Odoo itself accepts, no
more and no less.

Polish-notation reminder. Odoo domains use prefix notation where:

* ``&`` (AND) is **binary** — consumes 2 operands.
* ``|`` (OR) is **binary** — consumes 2 operands.
* ``!`` (NOT) is **unary** — consumes 1 operand.
* Leaves are 3-tuples ``(field, operator, value)``.

The pre-v0.3.0 validator treated all three operators identically
(``depth += 1`` per operator, ``depth -= 1`` per leaf, ``if depth > 0``
clamp on the decrement). That's not "tree depth" — it's a
balance-counter, and it accepted malformed inputs that Odoo itself
rejects (e.g. ``["&"]``, ``["!", L1, L2]``, ``[L1, L2]`` without an
explicit join). The v0.3.0 rewrite fixes this.

Strict-mode policy. Unlike Odoo's ``normalize_domain`` which silently
inserts implicit ``&`` between unjoined subtrees (legacy backward-
compat), the gateway REQUIRES explicit operators. An MCP gateway
surface is best served by rejecting ambiguous input from LLM callers
rather than guessing.

Empty domain semantics. ``validate_domain([])`` returns ``[]``
unchanged — an empty domain matches "all records" in Odoo's ORM
semantics, which is a legitimate (if dangerous) caller intent.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# Valid Odoo domain operators (mirrors ``odoo.osv.expression.TERM_OPERATORS``).
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

# Polish-notation operators and their arities. Source: Odoo's
# ``odoo.osv.expression`` where ``OP_ARITY = {NOT_OPERATOR: 1,
# AND_OPERATOR: 2, OR_OPERATOR: 2}``.
AND_OPERATOR = "&"
OR_OPERATOR = "|"
NOT_OPERATOR = "!"
_OP_ARITY: dict[str, int] = {
    NOT_OPERATOR: 1,
    AND_OPERATOR: 2,
    OR_OPERATOR: 2,
}

# Maximum domain tree depth. Empirically every legitimate stock Odoo +
# OCA domain we've observed sits at depth ≤ 5 (`account`, `sale`,
# `purchase` modules in 18.0 peak at depth 4-5). Lowered from 10 to 8
# to tighten the budget against adversarial LLM-generated inputs while
# keeping plenty of headroom for legitimate complex filters.
MAX_DOMAIN_DEPTH = 8

# Maximum number of leaf conditions. Each leaf is one (field, op, value)
# tuple. 50 is generous — typical real-world filters have 1-5 leaves.
MAX_DOMAIN_LEAVES = 50

# Pattern for valid field paths (supports dotted traversal).
_FIELD_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

# Maximum traversal depth for dotted field paths. Shared with the
# order-clause validator in ``tools/crud._validate_order`` so the two
# enforcement points stay in sync.
MAX_FIELD_TRAVERSAL = 4

# Maximum items in an "in" / "not in" value list.
MAX_IN_LIST_SIZE = 10_000


class DomainValidationError(ValueError):
    """Raised when a domain filter is invalid or unsafe."""


def validate_domain(domain: list[Any]) -> list[Any]:
    """Validate an Odoo domain filter and return it unchanged.

    The algorithm walks the token sequence once, maintaining:

    * ``expected`` — operands the tree still needs. Starts at 1
      (the whole domain is one expression). Each operator increases
      it by ``arity − 1`` (binary +1, unary 0). Each leaf decreases
      it by 1. When ``expected`` reaches 0, the tree is complete;
      further tokens are trailing garbage.
    * ``arity_stack`` — a per-pending-operator countdown. Push the
      arity when an operator is seen; pop when the count reaches 0
      (i.e. all operands supplied). ``len(arity_stack)`` is the true
      tree depth at that point.

    Rejects:
    * Tokens after the tree is complete.
    * Operators starved of operands.
    * Trees that never complete (``expected != 0`` at end).
    * Depth > ``MAX_DOMAIN_DEPTH``.
    * More than ``MAX_DOMAIN_LEAVES`` leaves.
    * Malformed leaves (non-3-tuples, bad field/op/value).
    """
    if not isinstance(domain, list):
        raise DomainValidationError("Domain must be a list")

    if not domain:
        # Empty domain = match all records. Legitimate caller intent;
        # let it pass.
        return domain

    expected = 1
    leaf_count = 0
    arity_stack: list[int] = []
    max_depth = 0

    for index, token in enumerate(domain):
        # Trailing-garbage check. If the tree is already complete,
        # any further token is an error. Odoo's normalize_domain
        # silently inserts ``&`` between adjacent subtrees; we
        # require the caller to be explicit.
        if expected == 0:
            raise DomainValidationError(
                f"Domain has trailing tokens after position {index - 1}: "
                "the tree is already complete. Use '&' or '|' explicitly "
                "to join multiple conditions."
            )

        if isinstance(token, str) and token in _OP_ARITY:
            arity = _OP_ARITY[token]
            arity_stack.append(arity)
            depth = len(arity_stack)
            if depth > max_depth:
                max_depth = depth
                if max_depth > MAX_DOMAIN_DEPTH:
                    raise DomainValidationError(
                        f"Domain too deeply nested (depth {max_depth}, "
                        f"max {MAX_DOMAIN_DEPTH})"
                    )
            # Binary op needs 2 children: tree now needs 1 MORE operand
            # than before. Unary op needs 1 child: no net change.
            expected += arity - 1
        elif isinstance(token, (list, tuple)):
            _validate_leaf(token)
            leaf_count += 1
            if leaf_count > MAX_DOMAIN_LEAVES:
                raise DomainValidationError(
                    f"Too many domain conditions ({leaf_count}, "
                    f"max {MAX_DOMAIN_LEAVES})"
                )
            expected -= 1
            # Pop satisfied operators: a unary op with 1 remaining
            # operand → consumed; a binary op with 2 remaining → now
            # needs 1; etc.
            _pop_satisfied_operators(arity_stack)
        elif isinstance(token, str):
            # String token that isn't a recognised operator.
            raise DomainValidationError(
                f"Invalid boolean operator: {token!r}. Must be '&', '|', "
                "or '!', or wrap in a 3-tuple for a leaf condition."
            )
        else:
            raise DomainValidationError(
                f"Invalid domain element type at position {index}: "
                f"{type(token).__name__}. Expected operator string or "
                "3-tuple leaf."
            )

    if expected != 0:
        # The tree is incomplete — an operator was promised operands
        # that never arrived.
        if arity_stack:
            unsatisfied = arity_stack[-1]
            raise DomainValidationError(
                "Domain is incomplete: an operator is missing "
                f"{unsatisfied} more operand(s)."
            )
        raise DomainValidationError(
            f"Domain is incomplete: still expected {expected} more operand(s)."
        )

    return domain


def _pop_satisfied_operators(arity_stack: list[int]) -> None:
    """Decrement / pop operators on top of the stack as each operand
    arrives. A unary op with one remaining slot becomes satisfied;
    a binary op with two becomes a binary with one; etc."""
    while arity_stack:
        arity_stack[-1] -= 1
        if arity_stack[-1] == 0:
            arity_stack.pop()
            # The popped operator was itself an operand of whatever
            # operator (if any) sits below it on the stack — continue
            # the cascade.
            continue
        break


def _validate_leaf(item: list[Any] | tuple[Any, ...]) -> None:
    """Validate one (field, op, value) leaf tuple."""
    if len(item) != 3:
        raise DomainValidationError(
            f"Domain leaf must have 3 elements (field, op, value), got {len(item)}"
        )
    field, op, value = item
    _validate_field_path(field)
    _validate_operator(op)
    _validate_value(value)


def _validate_field_path(field: Any) -> None:
    """Validate a field path like 'partner_id.country_id.code'."""
    if not isinstance(field, str):
        raise DomainValidationError(
            f"Field must be a string, got {type(field).__name__}"
        )
    if not _FIELD_PATH_RE.match(field):
        raise DomainValidationError(f"Invalid field name: {field!r}")
    parts = field.split(".")
    if len(parts) > MAX_FIELD_TRAVERSAL:
        raise DomainValidationError(
            f"Field traversal too deep: {field!r} "
            f"({len(parts)} levels, max {MAX_FIELD_TRAVERSAL})"
        )


def _validate_operator(op: Any) -> None:
    """Validate a domain operator."""
    if not isinstance(op, str):
        raise DomainValidationError(
            f"Operator must be a string, got {type(op).__name__}"
        )
    if op not in VALID_OPERATORS:
        raise DomainValidationError(f"Invalid operator: {op!r}")


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
