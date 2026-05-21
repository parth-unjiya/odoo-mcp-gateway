"""Security middleware: orchestrates the full security pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .audit import AuditLogger
from .rate_limit import RateLimiter
from .rbac import RBACManager
from .restrictions import RestrictionChecker
from .sanitizer import ErrorSanitizer

_log = logging.getLogger(__name__)

# Operations that count as writes for rate limiting.
# Mutable set so plugins can register additional write tools.
_WRITE_TOOLS: set[str] = {
    "create_record",
    "update_record",
    "delete_record",
    "execute_method",
    "check_in",
    "check_out",
    "request_leave",
    "confirm_order",
    "create_ticket",
    "update_task_stage",
    "update_ticket_stage",
}

# Map tool names to operation types
_TOOL_OPERATION_MAP: dict[str, str] = {
    "search_read": "read",
    "get_record": "read",
    "search_count": "read",
    "read_group": "read",
    "list_models": "read",
    "get_model_fields": "read",
    "login": "auth",
    "create_record": "create",
    "update_record": "write",
    "delete_record": "delete",
    "execute_method": "write",
    "check_in": "create",
    "check_out": "write",
    "get_my_attendance": "read",
    "get_my_leaves": "read",
    "request_leave": "create",
    "get_my_profile": "read",
    "get_my_quotations": "read",
    "get_order_details": "read",
    "confirm_order": "write",
    "get_sales_summary": "read",
    "get_my_tasks": "read",
    "get_project_summary": "read",
    "update_task_stage": "write",
    "get_my_tickets": "read",
    "create_ticket": "create",
    "update_ticket_stage": "write",
}


_VALID_OPERATIONS = frozenset({"read", "write", "create", "delete", "auth"})


# Odoo context keys that can bypass security controls or audit trails.
# These MUST be stripped from any caller-supplied ``context`` kwarg that
# reaches Odoo via execute_kw. Each entry has a real-world bypass
# primitive associated:
#
#   active_test           - return archived (active=False) records
#   tracking_disable      - skip chatter / audit trail writes
#   mail_create_nolog     - skip create-time mail.message logging
#   mail_create_nosubscribe - skip auto-follower subscription
#   mail_notrack          - skip field-change tracking
#   force_company         - cross-company write
#   allowed_company_ids   - cross-company read
#   default_company_id    - cross-company create
#   no_reset_password     - silently disable password-reset email
#   import_compat         - relax type coercion / validation
#   check_move_validity   - skip accounting move validation
#
# Lifted from tools/crud.py so every tool that takes a ``context``
# kwarg (today only execute_method, but new plugin tools must use this
# helper) shares one canonical filter rather than re-declaring its own.
DANGEROUS_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "active_test",
        "tracking_disable",
        "mail_create_nolog",
        "mail_create_nosubscribe",
        "mail_notrack",
        "force_company",
        "allowed_company_ids",
        "default_company_id",
        "no_reset_password",
        "import_compat",
        "check_move_validity",
    }
)


def filter_dangerous_context_keys(
    context: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Return *(sanitised_context, stripped_keys)* for a caller-supplied context.

    The first element is a NEW dict suitable to pass to Odoo via
    ``execute_kw`` — the input is not mutated. The second element is
    the list of keys that were removed, so callers can echo them in
    dry-run previews or audit logs ("here's what we would have done").

    ``None`` or non-dict inputs are coerced to an empty context; this
    keeps the call site one-liner-friendly.
    """
    if not isinstance(context, dict):
        return {}, []
    stripped: list[str] = []
    safe: dict[str, Any] = {}
    for k, v in context.items():
        if k in DANGEROUS_CONTEXT_KEYS:
            stripped.append(k)
        else:
            safe[k] = v
    return safe, stripped


def register_tool_operation(tool_name: str, operation: str) -> None:
    """Register a single tool's operation type.

    Parameters
    ----------
    tool_name:
        Name of the tool (e.g. ``"my_plugin_tool"``).
    operation:
        Operation type: ``"read"``, ``"write"``, ``"create"``, ``"delete"``,
        or ``"auth"``.

    Raises
    ------
    ValueError
        If *operation* is not one of the valid operation types.
    """
    if operation not in _VALID_OPERATIONS:
        raise ValueError(
            f"Invalid operation {operation!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_OPERATIONS))}"
        )
    _TOOL_OPERATION_MAP[tool_name] = operation
    if operation in ("write", "create", "delete"):
        _WRITE_TOOLS.add(tool_name)


def register_tool_operations(mapping: dict[str, str]) -> None:
    """Bulk-register tool operation types.

    Parameters
    ----------
    mapping:
        Dict of ``{tool_name: operation}``.
    """
    for tool_name, operation in mapping.items():
        register_tool_operation(tool_name, operation)


class SecurityError(Exception):
    """Raised when a security check fails."""

    def __init__(self, message: str, code: str = "security_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class SecurityContext:
    """Security context for the current request."""

    session_id: str
    user_id: int
    user_login: str
    user_groups: list[str]
    is_admin: bool


class SecurityMiddleware:
    """Container for security pipeline components.

    Individual security checks are executed via :func:`security_gate` which
    is called at the start of every tool handler.  This class holds the
    configured security components so they can be accessed from
    :class:`~odoo_mcp_gateway.server.GatewayContext`.
    """

    def __init__(
        self,
        restrictions: RestrictionChecker,
        rbac: RBACManager,
        rate_limiter: RateLimiter,
        audit: AuditLogger,
        sanitizer: ErrorSanitizer,
    ) -> None:
        self._restrictions = restrictions
        self._rbac = rbac
        self._rate_limiter = rate_limiter
        self._audit = audit
        self._sanitizer = sanitizer


async def security_gate(
    gateway: Any,
    tool_name: str,
    session_id: str = "default",
) -> str | None:
    """Run pre-tool security checks: rate limit, RBAC tool access, audit.

    Returns None if allowed, or an error message string if blocked.
    Call this at the start of every tool handler.
    """
    # Extract user context from auth_managers (not top-level attrs).
    #
    # UAT v0.3.3 MED-2 (Odoo 19): previously this fell through to
    # ``next(iter(gateway.auth_managers.values()))`` unconditionally,
    # which (a) returned the WRONG manager when more than one session
    # existed and (b) caused intermittent "Tool requires group X" RBAC
    # failures immediately after login when an evicted prior manager
    # with empty groups happened to remain at the head of the dict.
    # The fix mirrors plugins.core.helpers._resolve_auth_manager:
    # prefer the contextvar-bound session, then single-session
    # fallback, then surface "Not authenticated".
    _user_groups: list[str] = []
    _is_admin: bool = False
    _user_id: int = 0
    _user_login: str = "unknown"
    if hasattr(gateway, "auth_managers") and gateway.auth_managers:
        _mgr: Any = None
        # Late-bound import: ``server`` and the security middleware
        # share a module dependency we don't want to create at import
        # time (server imports SecurityMiddleware). Resolving the
        # current-session helper lazily keeps the cycle broken.
        try:
            from odoo_mcp_gateway.server import get_current_session_key

            _key = get_current_session_key()
        except Exception:
            _key = None
        if _key is not None:
            _mgr = gateway.auth_managers.get(_key)
        # Fallback: stdio mode single-session resolution.
        if _mgr is None and len(gateway.auth_managers) == 1:
            _mgr = next(iter(gateway.auth_managers.values()))
        if _mgr is not None:
            _result = getattr(_mgr, "auth_result", None)
            if _result is not None:
                _user_groups = getattr(_result, "groups", [])
                _is_admin = getattr(_result, "is_admin", False)
                _user_id = getattr(_result, "uid", 0)
                _user_login = getattr(_result, "username", "unknown")

    # Require authentication for all tools except login and resources
    if _user_id == 0 and not tool_name.startswith(("login", "resource:")):
        return "Not authenticated"

    audit_logger = getattr(gateway, "audit_logger", None)
    operation = _TOOL_OPERATION_MAP.get(tool_name, "read")

    def _audit(result: str, error_msg: str = "") -> None:
        if audit_logger is None:
            return
        try:
            entry = AuditLogger.create_entry(
                session_id=session_id,
                user_id=_user_id,
                user_login=_user_login,
                tool=tool_name,
                operation=operation,
                result=result,
                error_message=error_msg or None,
            )
            audit_logger.log(entry)
        except Exception:
            _log.warning(
                "Audit logging failed for tool %s",
                tool_name,
                exc_info=True,
            )

    # Metrics surface (no-op stand-in when [observability] not installed).
    metrics = getattr(gateway, "metrics", None)

    # 1. Rate limit check
    rate_limiter = getattr(gateway, "rate_limiter", None)
    if rate_limiter is not None:
        is_write = tool_name in _WRITE_TOOLS
        allowed, rate_msg = rate_limiter.check(session_id, is_write=is_write)
        if not allowed:
            msg = str(rate_msg)
            _audit("denied", msg)
            if metrics is not None:
                metrics.rate_limit_rejections.labels(
                    kind="write" if is_write else "read"
                ).inc()
                metrics.tool_requests.labels(
                    tool=tool_name, status="rate_limited"
                ).inc()
            return msg

    # 2. RBAC tool access check
    rbac = getattr(gateway, "rbac", None)
    if rbac is not None:
        rbac_msg = rbac.check_tool_access(tool_name, _user_groups, _is_admin)
        if rbac_msg:
            msg = str(rbac_msg)
            _audit("denied", msg)
            if metrics is not None:
                metrics.tool_requests.labels(tool=tool_name, status="denied").inc()
            return msg

    # 3. Audit log allowed + record tool invocation. Latency is
    # recorded by the tool itself (the gate doesn't see the tool
    # finish, only its start).
    _audit("allowed")
    if metrics is not None:
        metrics.tool_requests.labels(tool=tool_name, status="allowed").inc()

    return None
