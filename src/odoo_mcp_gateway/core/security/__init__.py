"""Security enforcement: restrictions, RBAC, audit, rate limiting, sanitization."""

from __future__ import annotations

from .audit import AuditEntry, AuditLogger
from .config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
    load_config,
)
from .middleware import (
    DANGEROUS_CONTEXT_KEYS,
    SecurityContext,
    SecurityError,
    SecurityMiddleware,
    filter_dangerous_context_keys,
    register_tool_operation,
    register_tool_operations,
    security_gate,
)
from .rate_limit import LoginIpRateLimiter, LoginRateLimiter, RateLimiter, TokenBucket
from .rbac import RBACManager
from .restrictions import RestrictionChecker
from .sanitizer import ErrorSanitizer

__all__ = [
    "DANGEROUS_CONTEXT_KEYS",
    "AuditEntry",
    "AuditLogger",
    "ErrorSanitizer",
    "GatewayConfig",
    "LoginIpRateLimiter",
    "LoginRateLimiter",
    "ModelAccessConfig",
    "RBACConfig",
    "RateLimiter",
    "RBACManager",
    "RestrictionChecker",
    "RestrictionConfig",
    "SecurityContext",
    "SecurityError",
    "SecurityMiddleware",
    "TokenBucket",
    "filter_dangerous_context_keys",
    "load_config",
    "register_tool_operation",
    "register_tool_operations",
    "security_gate",
]
