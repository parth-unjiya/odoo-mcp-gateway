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
    SecurityContext,
    SecurityError,
    SecurityMiddleware,
    register_tool_operation,
    register_tool_operations,
    security_gate,
)
from .rate_limit import RateLimiter, TokenBucket
from .rbac import RBACManager
from .restrictions import RestrictionChecker
from .sanitizer import ErrorSanitizer

__all__ = [
    "AuditEntry",
    "AuditLogger",
    "ErrorSanitizer",
    "GatewayConfig",
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
    "load_config",
    "register_tool_operation",
    "register_tool_operations",
    "security_gate",
]
