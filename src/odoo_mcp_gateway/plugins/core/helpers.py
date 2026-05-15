"""Shared helpers for domain plugins."""

from __future__ import annotations

import re
from typing import Any

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.server import get_current_session_key


async def check_security_gate(context: Any, tool_name: str) -> str | None:
    """Run security gate checks (rate limit, RBAC tool access, audit).

    Returns None if allowed, error string if blocked.
    """
    session_key = get_current_session_key() or next(
        iter(context.auth_managers.keys()), "default"
    )
    return await security_gate(context, tool_name, session_key)


def get_client(context: Any) -> Any:
    """Extract the active Odoo client from the gateway context."""
    if not context.auth_managers:
        return None
    key = get_current_session_key()
    if key is not None and key in context.auth_managers:
        mgr = context.auth_managers[key]
    else:
        mgr = next(iter(context.auth_managers.values()))
    try:
        return mgr.get_active_client()
    except Exception:
        return None


def get_uid(context: Any) -> int:
    """Extract the current user ID from the gateway context."""
    if not context.auth_managers:
        return 0
    key = get_current_session_key()
    if key is not None and key in context.auth_managers:
        mgr = context.auth_managers[key]
    else:
        mgr = next(iter(context.auth_managers.values()))
    result = getattr(mgr, "auth_result", None)
    return getattr(result, "uid", 0) if result else 0


def get_auth_info(context: Any) -> tuple[bool, list[str]]:
    """Extract admin status and group list from context."""
    if not context.auth_managers:
        return False, []
    key = get_current_session_key()
    if key is not None and key in context.auth_managers:
        mgr = context.auth_managers[key]
    else:
        mgr = next(iter(context.auth_managers.values()))
    result = getattr(mgr, "auth_result", None)
    if result is None:
        return False, []
    return getattr(result, "is_admin", False), getattr(result, "groups", [])


def format_model_error(
    model: str,
    exc: Exception,
    alternate_models: list[str] | None = None,
) -> str | None:
    """Detect model-not-found errors and return a user-friendly message.

    Returns a descriptive error string if the exception indicates the model
    is not available (module not installed), or None if unrecognized.

    Args:
        model: The model name that was queried.
        exc: The exception raised by the Odoo client.
        alternate_models: Optional list of alternate model names to suggest
            (e.g. for installations that use a different module name).
    """
    msg = str(exc).lower()
    if (
        "does not exist" in msg
        or "not found" in msg
        or "404" in msg
        or msg.strip() == model  # v17 returns bare model name
    ):
        message = (
            f"Model '{model}' is not available. "
            "The required Odoo module may not be installed."
        )
        if alternate_models:
            message += (
                " — try checking alternate model names: "
                f"{', '.join(alternate_models)}"
            )
        return message
    return None


def check_plugin_modules(
    context: Any, plugin_name: str, required_models: list[str]
) -> str | None:
    """Check if plugin's required Odoo modules are installed.

    Returns an error message string when modules are missing, or None if
    everything is available and the tool may proceed.
    """
    registry = getattr(context, "plugin_registry", None)
    if registry is None:
        return None
    try:
        info = registry.get_plugin(plugin_name)
    except Exception:
        return None
    if info is None:
        return None
    missing = getattr(info, "missing_modules", None)
    if isinstance(missing, list) and missing:
        return (
            f"This tool requires Odoo modules that are not installed: "
            f"{', '.join(missing)}. "
            f"Please install them in your Odoo instance."
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
