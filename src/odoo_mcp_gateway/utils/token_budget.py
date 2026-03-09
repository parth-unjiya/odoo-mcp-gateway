"""Token-aware response management for LLM-friendly output.

# Reserved for future use -- not currently called by production code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Rough estimate: 1 token ~ 4 characters
CHARS_PER_TOKEN = 4


@dataclass
class TruncationResult:
    """Result of truncation with metadata."""

    data: Any
    truncated: bool
    total_records: int
    returned_records: int
    estimated_tokens: int
    message: str = field(default="")


class TokenBudget:
    """Manages response sizes to stay within token limits.

    AI clients (Claude, GPT) have context window limits. Large Odoo
    responses can easily exceed these. TokenBudget estimates response
    size and auto-truncates when needed.
    """

    def __init__(self, max_tokens: int = 4000) -> None:
        """
        Parameters
        ----------
        max_tokens: Maximum estimated tokens for a single response.
            Default 4000 is conservative -- leaves room for the AI's
            own reasoning in its context window.
        """
        self._max_tokens = max_tokens

    @property
    def max_tokens(self) -> int:
        """Return the configured maximum token budget."""
        return self._max_tokens

    def estimate_tokens(self, data: Any) -> int:
        """Estimate token count for a data structure.

        Uses character count / 4 as rough estimate.
        For dicts/lists, serializes to string first.
        """
        if data is None:
            return 0
        if isinstance(data, str):
            return max(1, len(data) // CHARS_PER_TOKEN)
        if isinstance(data, (int, float, bool)):
            return 1

        # For complex types, estimate from JSON representation
        try:
            text = json.dumps(data, default=str)
        except (TypeError, ValueError):
            text = repr(data)
        return max(1, len(text) // CHARS_PER_TOKEN)

    def truncate_records(
        self,
        records: list[dict[str, Any]],
        fields: list[str] | None = None,
        priority_fields: list[str] | None = None,
    ) -> TruncationResult:
        """Truncate a list of records to fit within token budget.

        Strategy:
        1. If records fit within budget -> return as-is
        2. If too large -> reduce records until they fit
        3. If a single record is too large -> trim fields

        Parameters
        ----------
        records: List of record dicts
        fields: If set, only keep these fields
        priority_fields: Fields to keep when trimming (name, id, state)
        """
        total = len(records)

        if not records:
            return TruncationResult(
                data=records,
                truncated=False,
                total_records=0,
                returned_records=0,
                estimated_tokens=0,
            )

        # Step 1: Apply field filter if specified
        if fields:
            records = [{k: v for k, v in r.items() if k in fields} for r in records]

        # Step 2: Check if it fits
        tokens = self.estimate_tokens(records)
        if tokens <= self._max_tokens:
            return TruncationResult(
                data=records,
                truncated=False,
                total_records=total,
                returned_records=len(records),
                estimated_tokens=tokens,
            )

        # Step 3: Binary search for max records that fit
        lo, hi = 1, len(records)
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            subset_tokens = self.estimate_tokens(records[:mid])
            if subset_tokens <= self._max_tokens:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        result_records = records[:best]

        # Step 4: If even one record is too big, trim fields
        if best == 1 and self.estimate_tokens(result_records) > self._max_tokens:
            result_records = [self._trim_record(result_records[0], priority_fields)]

        final_tokens = self.estimate_tokens(result_records)
        return TruncationResult(
            data=result_records,
            truncated=True,
            total_records=total,
            returned_records=len(result_records),
            estimated_tokens=final_tokens,
            message=(
                f"Response truncated: showing {len(result_records)} of {total} records"
            ),
        )

    def _trim_record(
        self,
        record: dict[str, Any],
        priority_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Trim a single record to fit budget by removing low-priority fields."""
        priority = set(priority_fields or ["id", "name", "display_name", "state"])
        priority.add("id")  # Always keep id

        # Keep priority fields, then add others until budget reached
        trimmed: dict[str, Any] = {}
        for key in priority:
            if key in record:
                trimmed[key] = record[key]

        remaining = {k: v for k, v in record.items() if k not in priority}
        for key, value in remaining.items():
            candidate = {**trimmed, key: value}
            if self.estimate_tokens(candidate) <= self._max_tokens:
                trimmed[key] = value
            else:
                break

        return trimmed

    def format_response(
        self,
        records: list[dict[str, Any]],
        model: str,
        total_count: int | None = None,
    ) -> dict[str, Any]:
        """Format records into a standard LLM-friendly response.

        Returns a dict with keys: model, records, count, and optionally
        total, truncated, message, hint.
        """
        result = self.truncate_records(records)

        response: dict[str, Any] = {
            "model": model,
            "records": result.data,
            "count": result.returned_records,
        }

        if total_count is not None:
            response["total"] = total_count

        if result.truncated:
            response["truncated"] = True
            response["message"] = result.message
            if total_count and total_count > result.returned_records:
                response["hint"] = (
                    f"Use offset={result.returned_records} to see more records"
                )

        return response
