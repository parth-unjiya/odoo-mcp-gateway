"""Security middleware: orchestrates the full security pipeline."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .audit import AuditLogger
from .rate_limit import RateLimiter
from .rbac import RBACManager
from .restrictions import RestrictionChecker
from .sanitizer import ErrorSanitizer

_log = logging.getLogger(__name__)

# Operations that count as writes for rate limiting
_WRITE_TOOLS = frozenset(
    {
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
)

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
    """Orchestrates the full security pipeline for each tool invocation.

    Pipeline:
    1. Rate limit check
    2. RBAC tool access check
    3. Model restriction check (if 'model' in params)
    4. Method restriction check (if tool is 'execute_method')
    5. Field write check (if tool writes fields)
    6. Sanitize write values (RBAC)
    7. Execute handler
    8. Filter response fields (RBAC)
    9. Audit log
    On error: sanitize error, audit log, re-raise.
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

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        context: SecurityContext,
        handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        """Execute a tool through the full security pipeline."""
        start = time.monotonic()
        model = params.get("model")
        is_write = tool_name in _WRITE_TOOLS
        operation = _TOOL_OPERATION_MAP.get(tool_name, "read")

        try:
            # 1. Rate limit check
            allowed, rate_msg = self._rate_limiter.check(
                context.session_id, is_write=is_write
            )
            if not allowed:
                raise SecurityError(rate_msg, "rate_limited")

            # 2. RBAC tool access check
            rbac_msg = self._rbac.check_tool_access(
                tool_name, context.user_groups, context.is_admin
            )
            if rbac_msg:
                raise SecurityError(rbac_msg, "access_denied")

            # 3. Model restriction check
            if model:
                restriction_msg = self._restrictions.check_model_access(
                    model, operation, context.is_admin
                )
                if restriction_msg:
                    raise SecurityError(restriction_msg, "model_restricted")

            # 4. Method restriction check
            if tool_name == "execute_method":
                method = params.get("method", "")
                if model and method:
                    method_msg = self._restrictions.check_method_access(
                        model, method, context.is_admin
                    )
                    if method_msg:
                        raise SecurityError(method_msg, "method_restricted")

            # 5. Field write checks
            if is_write and model:
                values = params.get("values", {})
                if isinstance(values, dict):
                    for field_name in list(values.keys()):
                        field_msg = self._restrictions.check_field_write(
                            model, field_name, context.is_admin
                        )
                        if field_msg:
                            raise SecurityError(field_msg, "field_restricted")

            # 6. Sanitize write values (RBAC field filtering)
            if is_write and model:
                values = params.get("values", {})
                if isinstance(values, dict):
                    params = dict(params)
                    params["values"] = self._rbac.sanitize_write_values(
                        values, model, context.user_groups, context.is_admin
                    )

            # 7. Execute handler
            result = await handler(**params)

            # 8. Filter response fields
            if model and isinstance(result, list):
                result = self._rbac.filter_response_fields(
                    result, model, context.user_groups, context.is_admin
                )

            # 9. Audit success
            duration_ms = (time.monotonic() - start) * 1000
            entry = AuditLogger.create_entry(
                session_id=context.session_id,
                user_id=context.user_id,
                user_login=context.user_login,
                tool=tool_name,
                model=model,
                operation=operation,
                args=params,
                result="success",
                duration_ms=duration_ms,
            )
            self._audit.log(entry)

            return result

        except SecurityError as sec_exc:
            # Security errors: audit and re-raise as-is
            duration_ms = (time.monotonic() - start) * 1000
            entry = AuditLogger.create_entry(
                session_id=context.session_id,
                user_id=context.user_id,
                user_login=context.user_login,
                tool=tool_name,
                model=model,
                operation=operation,
                args=params,
                result="denied",
                duration_ms=duration_ms,
                error_message=str(sec_exc),
            )
            self._audit.log(entry)
            raise

        except Exception as exc:
            # Unexpected errors: sanitize, audit, re-raise
            duration_ms = (time.monotonic() - start) * 1000
            safe_message = self._sanitizer.sanitize_exception(exc)
            entry = AuditLogger.create_entry(
                session_id=context.session_id,
                user_id=context.user_id,
                user_login=context.user_login,
                tool=tool_name,
                model=model,
                operation=operation,
                args=params,
                result="error",
                duration_ms=duration_ms,
                error_message=safe_message,
            )
            self._audit.log(entry)
            raise SecurityError(safe_message, "internal_error") from exc


async def security_gate(
    gateway: Any,
    tool_name: str,
    session_id: str = "default",
) -> str | None:
    """Run pre-tool security checks: rate limit, RBAC tool access, audit.

    Returns None if allowed, or an error message string if blocked.
    Call this at the start of every tool handler.
    """
    # Extract user context from auth_managers (not top-level attrs)
    _user_groups: list[str] = []
    _is_admin: bool = False
    _user_id: int = 0
    _user_login: str = "unknown"
    if hasattr(gateway, "auth_managers") and gateway.auth_managers:
        _mgr = next(iter(gateway.auth_managers.values()), None)
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

    # 1. Rate limit check
    rate_limiter = getattr(gateway, "rate_limiter", None)
    if rate_limiter is not None:
        is_write = tool_name in _WRITE_TOOLS
        allowed, rate_msg = rate_limiter.check(session_id, is_write=is_write)
        if not allowed:
            msg = str(rate_msg)
            _audit("denied", msg)
            return msg

    # 2. RBAC tool access check
    rbac = getattr(gateway, "rbac", None)
    if rbac is not None:
        rbac_msg = rbac.check_tool_access(tool_name, _user_groups, _is_admin)
        if rbac_msg:
            msg = str(rbac_msg)
            _audit("denied", msg)
            return msg

    # 3. Audit log allowed
    _audit("allowed")

    return None
