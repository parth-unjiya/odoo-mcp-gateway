"""Tests for the security middleware."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.core.security.middleware import (
    _TOOL_OPERATION_MAP,
    _WRITE_TOOLS,
    SecurityMiddleware,
    register_tool_operation,
    register_tool_operations,
)
from odoo_mcp_gateway.core.security.rate_limit import RateLimiter
from odoo_mcp_gateway.core.security.rbac import RBACManager
from odoo_mcp_gateway.core.security.restrictions import RestrictionChecker
from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer


@pytest.fixture()
def restriction_config() -> RestrictionConfig:
    return RestrictionConfig(
        always_blocked=["ir.config_parameter"],
        admin_only=["res.users"],
        admin_write_only=["res.company"],
        blocked_methods=["sudo"],
        blocked_write_fields=["password"],
    )


@pytest.fixture()
def model_access_config() -> ModelAccessConfig:
    return ModelAccessConfig(
        default_policy="deny",
        stock_models={
            "full_crud": ["res.partner", "sale.order"],
            "read_only": ["res.currency"],
        },
        allowed_methods={
            "sale.order": ["action_confirm", "action_cancel"],
        },
    )


@pytest.fixture()
def rbac_config() -> RBACConfig:
    return RBACConfig(
        tool_group_requirements={
            "delete_record": ["base.group_system"],
            "create_record": ["base.group_user"],
        },
        sensitive_fields={
            "hr.employee": {
                "fields": ["wage"],
                "required_group": "hr.group_hr_manager",
            },
        },
    )


@pytest.fixture()
def restrictions(
    restriction_config: RestrictionConfig,
    model_access_config: ModelAccessConfig,
) -> RestrictionChecker:
    return RestrictionChecker(restriction_config, model_access_config)


@pytest.fixture()
def rbac(
    rbac_config: RBACConfig, model_access_config: ModelAccessConfig
) -> RBACManager:
    return RBACManager(rbac_config, model_access_config)


@pytest.fixture()
def rate_limiter() -> RateLimiter:
    return RateLimiter(global_rate=100, write_rate=50)


@pytest.fixture()
def sanitizer() -> ErrorSanitizer:
    return ErrorSanitizer()


@pytest.fixture()
def middleware(
    restrictions: RestrictionChecker,
    rbac: RBACManager,
    rate_limiter: RateLimiter,
    sanitizer: ErrorSanitizer,
) -> SecurityMiddleware:
    from odoo_mcp_gateway.core.security.audit import AuditLogger

    audit = AuditLogger(backend="logger")
    return SecurityMiddleware(restrictions, rbac, rate_limiter, audit, sanitizer)


# ── SecurityMiddleware holds components ───────────────────────────


class TestSecurityMiddlewareComponents:
    def test_middleware_exposes_components(
        self, middleware: SecurityMiddleware
    ) -> None:
        assert middleware._restrictions is not None
        assert middleware._rbac is not None
        assert middleware._rate_limiter is not None
        assert middleware._audit is not None
        assert middleware._sanitizer is not None


# ── Plugin operation type registration ────────────────────────────


class TestRegisterToolOperation:
    def test_register_read_operation(self) -> None:
        register_tool_operation("my_plugin_read", "read")
        assert _TOOL_OPERATION_MAP["my_plugin_read"] == "read"
        assert "my_plugin_read" not in _WRITE_TOOLS
        # Cleanup
        _TOOL_OPERATION_MAP.pop("my_plugin_read", None)

    def test_register_write_operation_adds_to_write_tools(self) -> None:
        register_tool_operation("my_plugin_write", "write")
        assert _TOOL_OPERATION_MAP["my_plugin_write"] == "write"
        assert "my_plugin_write" in _WRITE_TOOLS
        # Cleanup
        _TOOL_OPERATION_MAP.pop("my_plugin_write", None)
        _WRITE_TOOLS.discard("my_plugin_write")

    def test_register_create_operation_adds_to_write_tools(self) -> None:
        register_tool_operation("my_plugin_create", "create")
        assert _TOOL_OPERATION_MAP["my_plugin_create"] == "create"
        assert "my_plugin_create" in _WRITE_TOOLS
        # Cleanup
        _TOOL_OPERATION_MAP.pop("my_plugin_create", None)
        _WRITE_TOOLS.discard("my_plugin_create")

    def test_register_delete_operation_adds_to_write_tools(self) -> None:
        register_tool_operation("my_plugin_delete", "delete")
        assert _TOOL_OPERATION_MAP["my_plugin_delete"] == "delete"
        assert "my_plugin_delete" in _WRITE_TOOLS
        # Cleanup
        _TOOL_OPERATION_MAP.pop("my_plugin_delete", None)
        _WRITE_TOOLS.discard("my_plugin_delete")

    def test_register_auth_operation(self) -> None:
        register_tool_operation("my_auth_tool", "auth")
        assert _TOOL_OPERATION_MAP["my_auth_tool"] == "auth"
        assert "my_auth_tool" not in _WRITE_TOOLS
        # Cleanup
        _TOOL_OPERATION_MAP.pop("my_auth_tool", None)

    def test_invalid_operation_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid operation"):
            register_tool_operation("bad_tool", "execute")

    def test_overwrite_existing_operation(self) -> None:
        register_tool_operation("overwrite_tool", "read")
        register_tool_operation("overwrite_tool", "write")
        assert _TOOL_OPERATION_MAP["overwrite_tool"] == "write"
        assert "overwrite_tool" in _WRITE_TOOLS
        # Cleanup
        _TOOL_OPERATION_MAP.pop("overwrite_tool", None)
        _WRITE_TOOLS.discard("overwrite_tool")


class TestRegisterToolOperations:
    def test_bulk_registration(self) -> None:
        mapping = {
            "bulk_read": "read",
            "bulk_write": "write",
            "bulk_create": "create",
        }
        register_tool_operations(mapping)
        assert _TOOL_OPERATION_MAP["bulk_read"] == "read"
        assert _TOOL_OPERATION_MAP["bulk_write"] == "write"
        assert _TOOL_OPERATION_MAP["bulk_create"] == "create"
        assert "bulk_write" in _WRITE_TOOLS
        assert "bulk_create" in _WRITE_TOOLS
        assert "bulk_read" not in _WRITE_TOOLS
        # Cleanup
        for key in mapping:
            _TOOL_OPERATION_MAP.pop(key, None)
            _WRITE_TOOLS.discard(key)

    def test_bulk_with_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid operation"):
            register_tool_operations({"ok_tool": "read", "bad_tool": "destroy"})
        # The valid one may or may not have been registered before error
        _TOOL_OPERATION_MAP.pop("ok_tool", None)

    def test_empty_mapping(self) -> None:
        register_tool_operations({})  # Should not raise
