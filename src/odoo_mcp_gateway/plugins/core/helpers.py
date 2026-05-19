"""Shared helpers for domain plugins."""

from __future__ import annotations

import re
from typing import Any

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.server import get_current_session_key


def _resolve_auth_manager(context: Any) -> Any | None:
    """Resolve the AuthManager for the CURRENT MCP request.

    Resolution order (strict — no silent fall-through to other users):

    1. If the ``_current_session_key`` ContextVar is SET, the contract
       is "use exactly that session." If the key is present in
       ``context.auth_managers``, return that manager. If the key has
       gone stale (session was popped by re-login or eviction) we MUST
       refuse rather than degrade to whatever single manager happens
       to remain — that residual manager almost certainly belongs to
       a different user.
    2. If the ContextVar is NOT set AND there is exactly ONE entry in
       ``context.auth_managers``, use it. This covers stdio mode where
       no contextvar is propagated and only one session can exist at a
       time (single-user-per-process enforcement at login).
    3. Otherwise return ``None`` — caller MUST treat this as
       "not authenticated" and refuse to operate.
    """
    if not context.auth_managers:
        return None
    key = get_current_session_key()
    if key is not None:
        # Contextvar set: bind strictly to that key. Stale key → refuse.
        return context.auth_managers.get(key)
    # No contextvar: the single-session case is the only safe resolution.
    if len(context.auth_managers) == 1:
        return next(iter(context.auth_managers.values()))
    # Multiple sessions, no key → ambiguous. Refuse rather than guess.
    return None


def get_auth_context(context: Any) -> tuple[Any, int, bool, list[str]] | None:
    """Atomically resolve client, uid, is_admin, and groups in one call.

    Returns a 4-tuple ``(client, uid, is_admin, groups)`` for the current
    request, or ``None`` if no unambiguous session can be resolved.

    Prefer this over separate ``get_client``/``get_uid``/``get_auth_info``
    calls — calling them separately could theoretically resolve to
    different sessions if the contextvar mutates between calls.
    """
    mgr = _resolve_auth_manager(context)
    if mgr is None:
        return None
    try:
        client = mgr.get_active_client()
    except Exception:
        return None
    result = getattr(mgr, "auth_result", None)
    if result is None:
        return None
    return (
        client,
        getattr(result, "uid", 0),
        getattr(result, "is_admin", False),
        getattr(result, "groups", []),
    )


async def check_security_gate(context: Any, tool_name: str) -> str | None:
    """Run security gate checks (rate limit, RBAC tool access, audit).

    Returns None if allowed, error string if blocked. Uses the same
    strict resolution rules as :func:`_resolve_auth_manager` — a
    contextvar that has gone stale will NOT silently fall back to
    a remaining session belonging to a different user.
    """
    session_key = get_current_session_key()
    if session_key is not None:
        # Contextvar set: only honour it if the session is still live.
        # Stale key falls through to ``default`` so rate limiting still
        # applies; never silently rebind to another user's session.
        if session_key not in context.auth_managers:
            session_key = "default"
    else:
        # No contextvar — safe single-session fallback only.
        if len(context.auth_managers) == 1:
            session_key = next(iter(context.auth_managers.keys()))
        else:
            session_key = "default"
    return await security_gate(context, tool_name, session_key)


def get_client(context: Any) -> Any:
    """Extract the active Odoo client for the current session.

    Returns None if no unambiguous session can be resolved — callers MUST
    treat this as "not authenticated" rather than silently picking a
    different user's session.
    """
    mgr = _resolve_auth_manager(context)
    if mgr is None:
        return None
    try:
        return mgr.get_active_client()
    except Exception:
        return None


def get_uid(context: Any) -> int:
    """Extract the current user ID for the resolved session.

    Returns 0 if no unambiguous session can be resolved.
    """
    mgr = _resolve_auth_manager(context)
    if mgr is None:
        return 0
    result = getattr(mgr, "auth_result", None)
    return getattr(result, "uid", 0) if result else 0


def get_auth_info(context: Any) -> tuple[bool, list[str]]:
    """Extract admin status and group list for the resolved session.

    Returns ``(False, [])`` if no unambiguous session can be resolved.
    """
    mgr = _resolve_auth_manager(context)
    if mgr is None:
        return False, []
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
                f" — try checking alternate model names: {', '.join(alternate_models)}"
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


async def get_valid_states(
    client: Any,
    model: str,
    state_field: str = "state",
) -> set[str] | None:
    """Fetch the live set of valid selection values for *state_field* on *model*.

    This is the runtime fallback for plugin state-whitelists that would
    otherwise drift between Odoo versions (e.g. hr.leave gained a new
    'validate1' state, sale.order has slightly different keys per ERP fork).
    Plugins call this and combine the result with their static set as
    defense-in-depth.

    Returns ``None`` when the field cannot be inspected — caller should
    fall back to the static set so a transient Odoo glitch never blocks
    a legitimate query.
    """
    try:
        fields = await client.execute_kw(
            model,
            "fields_get",
            [[state_field]],
            {"attributes": ["selection"]},
        )
    except Exception:
        return None
    if not isinstance(fields, dict):
        return None
    field_info = fields.get(state_field)
    if not isinstance(field_info, dict):
        return None
    selection = field_info.get("selection")
    if not isinstance(selection, list):
        return None
    valid: set[str] = set()
    for item in selection:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            value = item[0]
            if isinstance(value, str):
                valid.add(value)
    return valid or None


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
