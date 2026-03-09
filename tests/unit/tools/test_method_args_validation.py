"""Tests for _validate_method_args in crud.py."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.tools.crud import _validate_method_args


class TestValidateMethodArgsValid:
    def test_valid_simple_args(self) -> None:
        """Simple args and kwargs should pass validation."""
        _validate_method_args([1, "hello", True], {"key": "value"})

    def test_empty_args(self) -> None:
        """Empty args and kwargs should be fine."""
        _validate_method_args([], {})

    def test_accepts_moderate_nesting(self) -> None:
        """Nesting depth below the limit should be accepted."""
        nested = {"a": {"b": {"c": "ok"}}}
        _validate_method_args([nested], {})  # depth 3 is well under 10

    def test_accepts_moderate_list_nesting(self) -> None:
        """Moderate list nesting should be accepted."""
        nested = [[["inner"]]]
        _validate_method_args(nested, {})

    def test_accepts_mixed_nesting(self) -> None:
        """Mixed dict/list nesting below limit should be accepted."""
        nested = {"a": [{"b": [1, 2, 3]}]}
        _validate_method_args([nested], {})

    def test_accepts_reasonable_size(self) -> None:
        """Args with reasonable size should pass."""
        data = {"key": "x" * 1000}  # 1000 chars is well under 100_000
        _validate_method_args([], data)

    def test_accepts_list_of_ids(self) -> None:
        """A list of integer IDs is a common pattern and should pass."""
        _validate_method_args([list(range(100))], {})

    def test_accepts_none_values(self) -> None:
        """None values in args should be fine."""
        _validate_method_args([None, None], {"key": None})

    def test_accepts_booleans_and_floats(self) -> None:
        """Various primitive types should be accepted."""
        _validate_method_args([True, False, 3.14, 0], {"flag": True})


class TestValidateMethodArgsOversized:
    def test_rejects_oversized_kwargs(self) -> None:
        """Args exceeding the size limit should raise ValueError."""
        huge = {"data": "x" * 200_000}
        with pytest.raises(ValueError, match="too large"):
            _validate_method_args([], huge)

    def test_rejects_oversized_args(self) -> None:
        """Oversized positional args should also be rejected."""
        huge_list = ["x" * 200_000]
        with pytest.raises(ValueError, match="too large"):
            _validate_method_args(huge_list, {})

    def test_rejects_combined_oversized(self) -> None:
        """Combined args + kwargs over the size limit should be rejected."""
        big_args = ["x" * 60_000]
        big_kwargs = {"key": "y" * 60_000}
        with pytest.raises(ValueError, match="too large"):
            _validate_method_args(big_args, big_kwargs)

    def test_just_under_limit_passes(self) -> None:
        """Args just under the limit should pass."""
        # Each JSON serialized will add some overhead, but a 90k string
        # in kwargs alone should be under the 100k limit
        data = {"data": "x" * 90_000}
        _validate_method_args([], data)


class TestValidateMethodArgsDeeplyNested:
    def test_rejects_deeply_nested_dict(self) -> None:
        """Deeply nested dicts should raise ValueError."""
        nested: dict = {}
        current = nested
        for i in range(15):
            current[f"level_{i}"] = {}
            current = current[f"level_{i}"]
        current["leaf"] = "deep"

        with pytest.raises(ValueError, match="deeply nested"):
            _validate_method_args([nested], {})

    def test_rejects_deeply_nested_list(self) -> None:
        """Deeply nested lists should raise ValueError."""
        deep: list = [42]
        for _ in range(12):
            deep = [deep]

        with pytest.raises(ValueError, match="deeply nested"):
            _validate_method_args(deep, {})

    def test_rejects_deeply_nested_in_kwargs(self) -> None:
        """Deeply nested kwargs should also be rejected."""
        nested: dict = {}
        current = nested
        for i in range(15):
            current[f"k{i}"] = {}
            current = current[f"k{i}"]
        current["leaf"] = "value"

        with pytest.raises(ValueError, match="deeply nested"):
            _validate_method_args([], nested)

    def test_rejects_mixed_deep_nesting(self) -> None:
        """Mixed dict/list nesting beyond the limit should be rejected."""
        # Build alternating dict/list nesting > 10 levels
        obj: dict | list = "leaf"
        for i in range(12):
            if i % 2 == 0:
                obj = [obj]
            else:
                obj = {"inner": obj}

        with pytest.raises(ValueError, match="deeply nested"):
            _validate_method_args([obj], {})

    def test_exact_boundary_depth_10_passes(self) -> None:
        """Exactly at the depth limit should still pass (boundary test).

        _check_depth raises when depth > _MAX_ARG_DEPTH (10).
        The outer _check_depth(args) starts at depth=0 for the args list.
        With [nested], the list items are checked at depth=1.
        So 'nested' with 8 dict levels inside reaches depth 9 for the innermost
        dict, and its leaf value reaches depth 10 -- exactly _MAX_ARG_DEPTH.
        """
        # Build 8 levels of dict nesting. When wrapped in a list, total:
        # [list](0) -> nested_dict(1) -> level_0(2) -> ... -> level_7(9) -> leaf(10)
        nested: dict = {}
        current = nested
        for i in range(8):
            current[f"level_{i}"] = {}
            current = current[f"level_{i}"]
        current["leaf"] = "value"

        # This should NOT raise -- depth reaches exactly 10 (not >10)
        _validate_method_args([nested], {})

    def test_depth_11_fails(self) -> None:
        """11 levels of nesting should be rejected."""
        nested: dict = {}
        current = nested
        for i in range(11):
            current[f"level_{i}"] = {}
            current = current[f"level_{i}"]
        current["leaf"] = "value"

        with pytest.raises(ValueError, match="deeply nested"):
            _validate_method_args([nested], {})
