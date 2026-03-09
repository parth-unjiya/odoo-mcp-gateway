"""Shared helpers for domain plugins."""

from __future__ import annotations

import re
from typing import Any

from odoo_mcp_gateway.core.security import security_gate


async def check_security_gate(context: Any, tool_name: str) -> str | None:
    """Run security gate checks (rate limit, RBAC tool access, audit).

    Returns None if allowed, error string if blocked.
    """
    session_key = next(iter(context.auth_managers.keys()), "default")
    return await security_gate(context, tool_name, session_key)


def get_client(context: Any) -> Any:
    """Extract the active Odoo client from the gateway context."""
    if not context.auth_managers:
        return None
    mgr = next(iter(context.auth_managers.values()))
    try:
        return mgr.get_active_client()
    except Exception:
        return None


def get_uid(context: Any) -> int:
    """Extract the current user ID from the gateway context."""
    if not context.auth_managers:
        return 0
    mgr = next(iter(context.auth_managers.values()))
    result = getattr(mgr, "auth_result", None)
    return getattr(result, "uid", 0) if result else 0


def get_auth_info(context: Any) -> tuple[bool, list[str]]:
    """Extract admin status and group list from context."""
    if not context.auth_managers:
        return False, []
    mgr = next(iter(context.auth_managers.values()))
    result = getattr(mgr, "auth_result", None)
    if result is None:
        return False, []
    return getattr(result, "is_admin", False), getattr(result, "groups", [])


def format_model_error(model: str, exc: Exception) -> str | None:
    """Detect model-not-found errors and return a user-friendly message.

    Returns a descriptive error string if the exception indicates the model
    is not available (module not installed), or None if unrecognized.
    """
    msg = str(exc).lower()
    if (
        "does not exist" in msg
        or "not found" in msg
        or "404" in msg
        or msg.strip() == model  # v17 returns bare model name
    ):
        return (
            f"Model '{model}' is not available. "
            "The required Odoo module may not be installed."
        )
    return None


def next_month(month: str) -> str:
    """Return first day of the month after the given 'YYYY-MM' string."""
    if not month or not re.match(r"^\d{4}-\d{2}$", month):
        raise ValueError(f"Invalid month format: {month!r}. Expected 'YYYY-MM'.")
    parts = month.split("-")
    year, mon = int(parts[0]), int(parts[1])
    if mon < 1 or mon > 12:
        raise ValueError(f"Invalid month: {mon}")
    if mon == 12:
        return f"{year + 1}-01-01 00:00:00"
    return f"{year}-{mon + 1:02d}-01 00:00:00"
