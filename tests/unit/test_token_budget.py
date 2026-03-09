"""Tests for token budget management."""

from __future__ import annotations

from odoo_mcp_gateway.utils.token_budget import (
    CHARS_PER_TOKEN,
    TokenBudget,
    TruncationResult,
)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    """Tests for TokenBudget.estimate_tokens."""

    def test_estimate_tokens_string(self) -> None:
        budget = TokenBudget()
        # 20 chars / 4 = 5 tokens
        assert budget.estimate_tokens("a" * 20) == 20 // CHARS_PER_TOKEN

    def test_estimate_tokens_empty_string(self) -> None:
        budget = TokenBudget()
        # Empty string -> max(1, 0) = 1
        assert budget.estimate_tokens("") == 1

    def test_estimate_tokens_short_string(self) -> None:
        budget = TokenBudget()
        # "hi" -> 2 chars / 4 = 0 -> max(1, 0) = 1
        assert budget.estimate_tokens("hi") == 1

    def test_estimate_tokens_dict(self) -> None:
        budget = TokenBudget()
        data = {"name": "Test", "id": 1}
        tokens = budget.estimate_tokens(data)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_estimate_tokens_list(self) -> None:
        budget = TokenBudget()
        data = [{"id": 1}, {"id": 2}, {"id": 3}]
        tokens = budget.estimate_tokens(data)
        assert tokens > 0

    def test_estimate_tokens_none(self) -> None:
        budget = TokenBudget()
        assert budget.estimate_tokens(None) == 0

    def test_estimate_tokens_number(self) -> None:
        budget = TokenBudget()
        assert budget.estimate_tokens(42) == 1
        assert budget.estimate_tokens(3.14) == 1

    def test_estimate_tokens_bool(self) -> None:
        budget = TokenBudget()
        assert budget.estimate_tokens(True) == 1
        assert budget.estimate_tokens(False) == 1


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


class TestTruncateRecords:
    """Tests for TokenBudget.truncate_records."""

    def test_truncate_empty_records(self) -> None:
        budget = TokenBudget()
        result = budget.truncate_records([])
        assert result.data == []
        assert result.truncated is False
        assert result.total_records == 0
        assert result.returned_records == 0
        assert result.estimated_tokens == 0

    def test_truncate_within_budget(self) -> None:
        budget = TokenBudget(max_tokens=4000)
        records = [{"id": i, "name": f"Record {i}"} for i in range(3)]
        result = budget.truncate_records(records)
        assert result.truncated is False
        assert result.returned_records == 3
        assert result.total_records == 3
        assert len(result.data) == 3

    def test_truncate_exceeds_budget(self) -> None:
        budget = TokenBudget(max_tokens=50)
        records = [
            {"id": i, "name": f"Record {i}", "description": "x" * 200}
            for i in range(20)
        ]
        result = budget.truncate_records(records)
        assert result.truncated is True
        assert result.returned_records < 20
        assert result.total_records == 20
        assert result.estimated_tokens <= 50

    def test_truncate_with_field_filter(self) -> None:
        budget = TokenBudget(max_tokens=4000)
        records = [{"id": 1, "name": "A", "description": "long text", "extra": 99}]
        result = budget.truncate_records(records, fields=["id", "name"])
        assert result.data == [{"id": 1, "name": "A"}]
        assert result.truncated is False

    def test_truncate_single_large_record_trimmed(self) -> None:
        budget = TokenBudget(max_tokens=20)
        records = [
            {
                "id": 1,
                "name": "Test",
                "description": "x" * 500,
                "notes": "y" * 500,
            }
        ]
        result = budget.truncate_records(records)
        assert result.truncated is True
        assert result.returned_records == 1
        # The record should be trimmed -- id is always kept
        assert "id" in result.data[0]

    def test_truncate_preserves_priority_fields(self) -> None:
        budget = TokenBudget(max_tokens=30)
        records = [
            {
                "id": 1,
                "name": "Important",
                "state": "draft",
                "huge_field": "x" * 500,
            }
        ]
        result = budget.truncate_records(
            records, priority_fields=["id", "name", "state"]
        )
        assert result.truncated is True
        rec = result.data[0]
        assert rec.get("id") == 1
        assert rec.get("name") == "Important"

    def test_truncate_result_metadata(self) -> None:
        budget = TokenBudget(max_tokens=4000)
        records = [{"id": 1}]
        result = budget.truncate_records(records)
        assert isinstance(result, TruncationResult)
        assert isinstance(result.estimated_tokens, int)
        assert result.estimated_tokens > 0

    def test_truncate_message_when_truncated(self) -> None:
        budget = TokenBudget(max_tokens=20)
        records = [{"id": i, "data": "x" * 100} for i in range(10)]
        result = budget.truncate_records(records)
        assert result.truncated is True
        assert "truncated" in result.message.lower()
        assert "of 10" in result.message

    def test_truncate_no_message_when_not_truncated(self) -> None:
        budget = TokenBudget(max_tokens=4000)
        records = [{"id": 1}]
        result = budget.truncate_records(records)
        assert result.truncated is False
        assert result.message == ""


# ---------------------------------------------------------------------------
# Format response
# ---------------------------------------------------------------------------


class TestFormatResponse:
    """Tests for TokenBudget.format_response."""

    def test_format_response_basic(self) -> None:
        budget = TokenBudget()
        records = [{"id": 1, "name": "Test"}]
        resp = budget.format_response(records, "sale.order")
        assert resp["model"] == "sale.order"
        assert resp["count"] == 1
        assert resp["records"] == [{"id": 1, "name": "Test"}]

    def test_format_response_with_total(self) -> None:
        budget = TokenBudget()
        records = [{"id": 1}]
        resp = budget.format_response(records, "res.partner", total_count=100)
        assert resp["total"] == 100

    def test_format_response_truncated_has_hint(self) -> None:
        budget = TokenBudget(max_tokens=20)
        records = [{"id": i, "data": "x" * 100} for i in range(50)]
        resp = budget.format_response(records, "res.partner", total_count=200)
        assert resp.get("truncated") is True
        assert "hint" in resp
        assert "offset=" in resp["hint"]

    def test_format_response_not_truncated_no_hint(self) -> None:
        budget = TokenBudget(max_tokens=4000)
        records = [{"id": 1}]
        resp = budget.format_response(records, "res.partner")
        assert "truncated" not in resp
        assert "hint" not in resp

    def test_format_response_includes_model(self) -> None:
        budget = TokenBudget()
        resp = budget.format_response([{"id": 1}], "account.move")
        assert resp["model"] == "account.move"

    def test_format_response_truncated_message(self) -> None:
        budget = TokenBudget(max_tokens=20)
        records = [{"id": i, "data": "x" * 100} for i in range(10)]
        resp = budget.format_response(records, "res.partner")
        if resp.get("truncated"):
            assert "message" in resp


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for TokenBudget."""

    def test_large_max_tokens(self) -> None:
        budget = TokenBudget(max_tokens=1_000_000)
        records = [{"id": i, "name": f"Rec {i}"} for i in range(100)]
        result = budget.truncate_records(records)
        assert result.truncated is False
        assert result.returned_records == 100

    def test_small_max_tokens(self) -> None:
        budget = TokenBudget(max_tokens=1)
        records = [{"id": 1, "name": "Test"}]
        result = budget.truncate_records(records)
        assert result.truncated is True
        assert result.returned_records == 1

    def test_records_with_binary_data(self) -> None:
        budget = TokenBudget(max_tokens=4000)
        records = [{"id": 1, "binary_field": "base64encodeddata=="}]
        result = budget.truncate_records(records)
        assert result.returned_records == 1

    def test_max_tokens_property(self) -> None:
        budget = TokenBudget(max_tokens=500)
        assert budget.max_tokens == 500

    def test_default_max_tokens(self) -> None:
        budget = TokenBudget()
        assert budget.max_tokens == 4000
