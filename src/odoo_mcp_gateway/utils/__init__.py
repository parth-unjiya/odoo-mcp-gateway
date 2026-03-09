"""Shared utility functions."""

from __future__ import annotations

from odoo_mcp_gateway.utils.domain_builder import (
    DomainValidationError,
    validate_domain,
)
from odoo_mcp_gateway.utils.formatting import (
    format_records,
    normalize_datetime,
    summarize_records,
)
from odoo_mcp_gateway.utils.token_budget import (
    TokenBudget,
    TruncationResult,
)

__all__ = [
    "DomainValidationError",
    "TokenBudget",
    "TruncationResult",
    "format_records",
    "normalize_datetime",
    "summarize_records",
    "validate_domain",
]
