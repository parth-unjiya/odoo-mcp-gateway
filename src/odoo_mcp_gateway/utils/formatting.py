"""Response formatting utilities for LLM-friendly output."""

from __future__ import annotations

from typing import Any


def format_records(
    records: list[dict[str, Any]],
    model: str = "",
) -> str:
    """Format records as a concise, readable string for LLM consumption.

    For small record sets (<=5): detailed view
    For larger sets: summary table format
    """
    if not records:
        return f"No records found{' for ' + model if model else ''}."

    if len(records) <= 5:
        return _format_detailed(records, model)
    return _format_table(records, model)


def _format_detailed(records: list[dict[str, Any]], model: str) -> str:
    """Format each record with all fields shown."""
    lines: list[str] = []
    for i, rec in enumerate(records, 1):
        header = f"Record {i}"
        if "id" in rec:
            header += f" (ID: {rec['id']})"
        if "name" in rec or "display_name" in rec:
            name = rec.get("display_name") or rec.get("name", "")
            header += f" — {name}"
        lines.append(header)
        for key, value in rec.items():
            if key in ("id", "name", "display_name"):
                continue
            lines.append(f"  {key}: {_format_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _format_table(records: list[dict[str, Any]], model: str) -> str:
    """Format records as a markdown-style table."""
    if not records:
        return ""

    # Get columns from first record, limit to 8 most important
    all_cols = list(records[0].keys())
    priority = [
        "id",
        "name",
        "display_name",
        "state",
        "date_order",
        "create_date",
    ]
    cols = [c for c in priority if c in all_cols]
    cols += [c for c in all_cols if c not in cols]
    cols = cols[:8]

    # Header
    lines = [" | ".join(cols)]
    lines.append(" | ".join("---" for _ in cols))

    # Rows
    for rec in records:
        row: list[str] = []
        for col in cols:
            val = rec.get(col, "")
            row.append(_format_value(val, max_len=40))
        lines.append(" | ".join(row))

    return "\n".join(lines)


def _format_value(value: Any, max_len: int = 100) -> str:
    """Format a single value for display."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], int):
            # Many2one: (id, name)
            return str(value[1])
        if len(value) > 5:
            return f"[{len(value)} items]"
        return str(value)
    if isinstance(value, dict):
        return "{...}"

    text = str(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def normalize_datetime(value: str | None) -> str:
    """Normalize Odoo datetime string to ISO format.

    Odoo returns datetimes as "2026-03-09 14:30:00" (no timezone).
    Normalize to ISO 8601: "2026-03-09T14:30:00".
    """
    if not value:
        return ""
    # Replace space with T for ISO format
    return value.replace(" ", "T")


def summarize_records(
    records: list[dict[str, Any]],
    model: str,
    total_count: int | None = None,
) -> dict[str, Any]:
    """Create a summary of a record set for LLM context.

    Returns metadata about the records without the full data.
    """
    summary: dict[str, Any] = {
        "model": model,
        "returned": len(records),
    }

    if total_count is not None:
        summary["total"] = total_count
        if total_count > len(records):
            summary["has_more"] = True

    if records:
        summary["fields"] = list(records[0].keys())

        # Extract common status distribution
        for status_field in ("state", "status", "stage_id"):
            if status_field in records[0]:
                distribution: dict[str, int] = {}
                for rec in records:
                    val = rec.get(status_field)
                    if isinstance(val, (list, tuple)):
                        val = val[1] if len(val) > 1 else val[0]
                    key = str(val) if val else "unknown"
                    distribution[key] = distribution.get(key, 0) + 1
                summary["status_distribution"] = distribution
                break

    return summary
