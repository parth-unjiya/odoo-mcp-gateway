"""Comprehensive malformed-domain test matrix for v0.3.0 ADR-004.

Pre-v0.3.0 the depth counter conflated "operators - leaves balance"
with "tree depth," so it accepted many malformed inputs that Odoo
itself rejects. These tests pin the strict policy of the rewritten
validator: every input here MUST be rejected.

The matrix covers:
* Arity violations on binary `&` / `|`.
* Arity violations on unary `!`.
* Operator-only domains (zero leaves).
* Trailing garbage after a complete tree.
* Mixed-nesting bugs.
* Plus a smoke test for legitimate complex polish-form domains that
  MUST still be accepted.
"""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.utils.domain_builder import (
    DomainValidationError,
    validate_domain,
)

_LEAF1 = ("a", "=", 1)
_LEAF2 = ("b", "=", 2)
_LEAF3 = ("c", "=", 3)


class TestBinaryOperatorArityViolations:
    """`&` and `|` must consume exactly 2 operands."""

    @pytest.mark.parametrize(
        "domain",
        [
            ["&"],  # 0 operands
            ["&", _LEAF1],  # only 1 operand
            ["|"],
            ["|", _LEAF1],
            ["&", "&", _LEAF1, _LEAF2],  # inner & starved
            ["|", "&", _LEAF1, _LEAF2],  # outer | short one
        ],
        ids=[
            "and_zero",
            "and_one",
            "or_zero",
            "or_one",
            "nested_and_starved",
            "outer_or_starved",
        ],
    )
    def test_binary_op_arity_rejected(self, domain: list) -> None:
        with pytest.raises(DomainValidationError, match="incomplete"):
            validate_domain(domain)


class TestUnaryOperatorArityViolations:
    """`!` must consume exactly 1 operand."""

    def test_not_with_zero_operands_rejected(self) -> None:
        with pytest.raises(DomainValidationError, match="incomplete"):
            validate_domain(["!"])

    def test_not_followed_by_two_leaves_rejects_trailing(self) -> None:
        # ['!', leaf1, leaf2]: ! consumes leaf1, tree complete; leaf2
        # is trailing garbage. The strict policy refuses implicit AND.
        with pytest.raises(DomainValidationError, match="trailing tokens"):
            validate_domain(["!", _LEAF1, _LEAF2])

    def test_not_starved_by_inner_op_with_no_operands(self) -> None:
        # ['!', '&']: ! waits for an operand, & is one but is itself
        # starved → tree incomplete.
        with pytest.raises(DomainValidationError, match="incomplete"):
            validate_domain(["!", "&"])


class TestOperatorOnlyDomains:
    """Domains with operators but no leaves are always incomplete."""

    @pytest.mark.parametrize(
        "domain",
        [
            ["&", "&", "&"],
            ["|", "!", "&"],
            ["&", "|"],
            ["!"],
            ["&"],
        ],
        ids=["three_ands", "or_not_and", "and_or", "lone_not", "lone_and"],
    )
    def test_operator_only_rejected(self, domain: list) -> None:
        with pytest.raises(DomainValidationError, match="incomplete"):
            validate_domain(domain)


class TestTrailingGarbage:
    """No implicit AND — every additional subtree must be explicitly joined."""

    def test_two_leaves_without_join_rejected(self) -> None:
        # Odoo's normalize_domain silently inserts '&' here; we reject.
        with pytest.raises(DomainValidationError, match="trailing tokens"):
            validate_domain([_LEAF1, _LEAF2])

    def test_complete_subtree_then_operator(self) -> None:
        # First subtree is complete after _LEAF1; the trailing '&'
        # and _LEAF2 are garbage.
        with pytest.raises(DomainValidationError, match="trailing tokens"):
            validate_domain([_LEAF1, "&", _LEAF2])

    def test_complete_and_then_extra_leaf(self) -> None:
        # ['&', leaf1, leaf2, leaf3]: '&' takes leaf1 and leaf2,
        # tree complete; leaf3 is garbage.
        with pytest.raises(DomainValidationError, match="trailing tokens"):
            validate_domain(["&", _LEAF1, _LEAF2, _LEAF3])


class TestMixedNestingBugs:
    """Subtle malformedness that the old buggy validator silently accepted."""

    def test_or_short_one_operand(self) -> None:
        # ['|', '&', leaf, leaf] = | (and leaf leaf) ??? — | needs 2.
        with pytest.raises(DomainValidationError, match="incomplete"):
            validate_domain(["|", "&", _LEAF1, _LEAF2])

    def test_and_extra_leaf_for_or_inner(self) -> None:
        # ['&', '|', leaf1, leaf2, leaf3]: '&' wants 2 operands, first
        # is '| leaf1 leaf2' (complete OR), second is leaf3. Tree
        # closes correctly — this one IS valid. Sanity check.
        domain = ["&", "|", _LEAF1, _LEAF2, _LEAF3]
        assert validate_domain(domain) == domain


class TestValidComplexDomains:
    """Real-world-shaped polish-form domains MUST still parse cleanly."""

    def test_double_or_inside_and(self) -> None:
        # & (| L1 L2) (| L3 L4)
        domain = [
            "&",
            "|",
            ("partner_id.country_id.code", "=", "US"),
            ("partner_id.country_id.code", "=", "CA"),
            "|",
            ("state", "=", "draft"),
            ("state", "=", "sent"),
        ]
        assert validate_domain(domain) == domain

    def test_nested_not(self) -> None:
        # ! (& L1 L2)
        domain = ["!", "&", _LEAF1, _LEAF2]
        assert validate_domain(domain) == domain

    def test_or_of_three_via_explicit_pairs(self) -> None:
        # | (| L1 L2) L3
        domain = ["|", "|", _LEAF1, _LEAF2, _LEAF3]
        assert validate_domain(domain) == domain

    def test_deeply_nested_within_limit(self) -> None:
        # Right-recursive AND of 5 leaves: depth 4.
        domain = [
            "&",
            ("a", "=", 1),
            "&",
            ("b", "=", 2),
            "&",
            ("c", "=", 3),
            "&",
            ("d", "=", 4),
            ("e", "=", 5),
        ]
        assert validate_domain(domain) == domain


class TestErrorMessagesAreInformative:
    """Validation messages should help an AI / human caller diagnose."""

    def test_trailing_tokens_mentions_position(self) -> None:
        with pytest.raises(
            DomainValidationError, match=r"trailing tokens after position"
        ):
            validate_domain([_LEAF1, _LEAF2])

    def test_incomplete_mentions_operands_needed(self) -> None:
        with pytest.raises(DomainValidationError, match=r"missing.*operand"):
            validate_domain(["&", _LEAF1])

    def test_depth_error_mentions_max(self) -> None:
        deep = []
        for _ in range(9):  # MAX_DOMAIN_DEPTH=8, 9 stacked ANDs trips it
            deep.append("&")
            deep.append(_LEAF1)
        deep.append(_LEAF1)
        with pytest.raises(DomainValidationError, match=r"max \d+"):
            validate_domain(deep)
