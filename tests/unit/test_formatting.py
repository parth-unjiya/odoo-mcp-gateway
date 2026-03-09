"""Tests for response formatting utilities."""

from __future__ import annotations

from odoo_mcp_gateway.utils.formatting import (
    _format_value,
    format_records,
    normalize_datetime,
    summarize_records,
)

# ---------------------------------------------------------------------------
# format_records
# ---------------------------------------------------------------------------


class TestFormatRecords:
    """Tests for format_records."""

    def test_format_records_empty(self) -> None:
        result = format_records([])
        assert "No records found" in result

    def test_format_records_empty_with_model(self) -> None:
        result = format_records([], model="sale.order")
        assert "No records found" in result
        assert "sale.order" in result

    def test_format_records_single_detailed(self) -> None:
        records = [{"id": 1, "name": "Order 1", "state": "draft"}]
        result = format_records(records)
        assert "Record 1" in result
        assert "ID: 1" in result
        assert "Order 1" in result
        assert "state: draft" in result

    def test_format_records_multiple_detailed(self) -> None:
        records = [
            {"id": 1, "name": "A"},
            {"id": 2, "name": "B"},
            {"id": 3, "name": "C"},
        ]
        result = format_records(records)
        assert "Record 1" in result
        assert "Record 2" in result
        assert "Record 3" in result

    def test_format_records_table_format(self) -> None:
        records = [{"id": i, "name": f"Item {i}", "state": "draft"} for i in range(6)]
        result = format_records(records)
        # Table format should have header separator
        assert "---" in result
        # Should have column headers
        assert "id" in result
        assert "name" in result

    def test_format_records_five_uses_detailed(self) -> None:
        records = [{"id": i, "name": f"Rec {i}"} for i in range(5)]
        result = format_records(records)
        # 5 records use detailed format (<=5)
        assert "Record 1" in result

    def test_format_records_six_uses_table(self) -> None:
        records = [{"id": i, "name": f"Rec {i}"} for i in range(6)]
        result = format_records(records)
        # 6 records use table format (>5)
        assert "---" in result


# ---------------------------------------------------------------------------
# _format_value
# ---------------------------------------------------------------------------


class TestFormatValue:
    """Tests for _format_value."""

    def test_format_value_none(self) -> None:
        assert _format_value(None) == ""

    def test_format_value_false(self) -> None:
        assert _format_value(False) == "No"

    def test_format_value_bool_true(self) -> None:
        # bool check comes before None/False check since True is truthy
        # But isinstance(True, bool) is True and value is not False
        assert _format_value(True) == "Yes"

    def test_format_value_many2one(self) -> None:
        # Odoo many2one: (id, name)
        assert _format_value((5, "Partner A")) == "Partner A"
        assert _format_value([5, "Partner A"]) == "Partner A"

    def test_format_value_list_short(self) -> None:
        result = _format_value([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_format_value_list_long(self) -> None:
        result = _format_value(list(range(10)))
        assert "[10 items]" in result

    def test_format_value_long_string_truncated(self) -> None:
        long_str = "x" * 200
        result = _format_value(long_str, max_len=50)
        assert len(result) == 50
        assert result.endswith("...")

    def test_format_value_short_string_not_truncated(self) -> None:
        result = _format_value("hello")
        assert result == "hello"

    def test_format_value_dict(self) -> None:
        assert _format_value({"key": "val"}) == "{...}"

    def test_format_value_number(self) -> None:
        assert _format_value(42) == "42"
        assert _format_value(3.14) == "3.14"


# ---------------------------------------------------------------------------
# normalize_datetime
# ---------------------------------------------------------------------------


class TestNormalizeDatetime:
    """Tests for normalize_datetime."""

    def test_normalize_datetime(self) -> None:
        result = normalize_datetime("2026-03-09 14:30:00")
        assert result == "2026-03-09T14:30:00"

    def test_normalize_datetime_empty(self) -> None:
        assert normalize_datetime("") == ""
        assert normalize_datetime(None) == ""

    def test_normalize_datetime_already_iso(self) -> None:
        result = normalize_datetime("2026-03-09T14:30:00")
        assert result == "2026-03-09T14:30:00"

    def test_normalize_datetime_date_only(self) -> None:
        result = normalize_datetime("2026-03-09")
        assert result == "2026-03-09"


# ---------------------------------------------------------------------------
# summarize_records
# ---------------------------------------------------------------------------


class TestSummarizeRecords:
    """Tests for summarize_records."""

    def test_summarize_records(self) -> None:
        records = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        result = summarize_records(records, "res.partner")
        assert result["model"] == "res.partner"
        assert result["returned"] == 2
        assert result["fields"] == ["id", "name"]

    def test_summarize_records_with_total(self) -> None:
        records = [{"id": 1}]
        result = summarize_records(records, "sale.order", total_count=50)
        assert result["total"] == 50

    def test_summarize_records_has_more(self) -> None:
        records = [{"id": 1}]
        result = summarize_records(records, "sale.order", total_count=50)
        assert result["has_more"] is True

    def test_summarize_records_no_has_more_when_equal(self) -> None:
        records = [{"id": 1}]
        result = summarize_records(records, "sale.order", total_count=1)
        assert "has_more" not in result

    def test_summarize_records_status_distribution(self) -> None:
        records = [
            {"id": 1, "state": "draft"},
            {"id": 2, "state": "draft"},
            {"id": 3, "state": "done"},
        ]
        result = summarize_records(records, "sale.order")
        assert result["status_distribution"] == {"draft": 2, "done": 1}

    def test_summarize_records_status_distribution_many2one(self) -> None:
        records = [
            {"id": 1, "stage_id": (1, "New")},
            {"id": 2, "stage_id": (2, "In Progress")},
            {"id": 3, "stage_id": (1, "New")},
        ]
        result = summarize_records(records, "project.task")
        dist = result["status_distribution"]
        assert dist["New"] == 2
        assert dist["In Progress"] == 1

    def test_summarize_empty_records(self) -> None:
        result = summarize_records([], "res.partner")
        assert result["model"] == "res.partner"
        assert result["returned"] == 0
        assert "fields" not in result

    def test_format_table_columns_limited_to_8(self) -> None:
        records = [{f"field_{i}": f"val_{i}" for i in range(15)} for _ in range(6)]
        result = format_records(records)
        # Table header line should have at most 8 columns
        header_line = result.split("\n")[0]
        col_count = len(header_line.split(" | "))
        assert col_count <= 8

    def test_format_detailed_shows_all_fields(self) -> None:
        records = [
            {
                "id": 1,
                "name": "Test",
                "field_a": "a",
                "field_b": "b",
                "field_c": "c",
            }
        ]
        result = format_records(records)
        assert "field_a: a" in result
        assert "field_b: b" in result
        assert "field_c: c" in result
