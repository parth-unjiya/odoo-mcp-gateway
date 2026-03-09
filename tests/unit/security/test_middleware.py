"""Tests for the security middleware."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from odoo_mcp_gateway.core.security.audit import AuditLogger
from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.core.security.middleware import (
    SecurityContext,
    SecurityError,
    SecurityMiddleware,
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
def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(backend="file", log_path=str(tmp_path / "audit.log"))


@pytest.fixture()
def sanitizer() -> ErrorSanitizer:
    return ErrorSanitizer()


@pytest.fixture()
def middleware(
    restrictions: RestrictionChecker,
    rbac: RBACManager,
    rate_limiter: RateLimiter,
    audit: AuditLogger,
    sanitizer: ErrorSanitizer,
) -> SecurityMiddleware:
    return SecurityMiddleware(restrictions, rbac, rate_limiter, audit, sanitizer)


@pytest.fixture()
def admin_ctx() -> SecurityContext:
    return SecurityContext(
        session_id="sess-admin",
        user_id=1,
        user_login="admin",
        user_groups=["base.group_system", "base.group_user"],
        is_admin=True,
    )


@pytest.fixture()
def user_ctx() -> SecurityContext:
    return SecurityContext(
        session_id="sess-user",
        user_id=2,
        user_login="demo",
        user_groups=["base.group_user"],
        is_admin=False,
    )


# ── Full success pipeline ─────────────────────────────────────────


class TestSuccessPipeline:
    @pytest.mark.asyncio
    async def test_read_allowed_model(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock(return_value=[{"id": 1, "name": "Test"}])
        result = await middleware.execute(
            "search_read",
            {"model": "res.partner"},
            user_ctx,
            handler,
        )
        assert result == [{"id": 1, "name": "Test"}]
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_admin_full_access(
        self, middleware: SecurityMiddleware, admin_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock(return_value={"id": 1})
        result = await middleware.execute(
            "delete_record",
            {"model": "res.partner", "id": 1},
            admin_ctx,
            handler,
        )
        assert result == {"id": 1}

    @pytest.mark.asyncio
    async def test_write_operation_succeeds(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock(return_value={"id": 1})
        result = await middleware.execute(
            "create_record",
            {"model": "res.partner", "values": {"name": "New"}},
            user_ctx,
            handler,
        )
        assert result == {"id": 1}


# ── Rate limit blocks ─────────────────────────────────────────────


class TestRateLimitBlocks:
    @pytest.mark.asyncio
    async def test_rate_limit_denies(
        self,
        restrictions: RestrictionChecker,
        rbac: RBACManager,
        audit: AuditLogger,
        sanitizer: ErrorSanitizer,
        user_ctx: SecurityContext,
    ) -> None:
        rate_limiter = RateLimiter(global_rate=1, write_rate=1)
        mw = SecurityMiddleware(restrictions, rbac, rate_limiter, audit, sanitizer)
        handler = AsyncMock(return_value=[])

        await mw.execute("search_read", {"model": "res.partner"}, user_ctx, handler)
        with pytest.raises(SecurityError, match="Rate limit"):
            await mw.execute("search_read", {"model": "res.partner"}, user_ctx, handler)


# ── RBAC blocks ────────────────────────────────────────────────────


class TestRBACBlocks:
    @pytest.mark.asyncio
    async def test_tool_access_denied(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        with pytest.raises(SecurityError, match="requires one of"):
            await middleware.execute(
                "delete_record",
                {"model": "res.partner", "id": 1},
                user_ctx,
                handler,
            )
        handler.assert_not_called()


# ── Restriction blocks ─────────────────────────────────────────────


class TestRestrictionBlocks:
    @pytest.mark.asyncio
    async def test_blocked_model(
        self, middleware: SecurityMiddleware, admin_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        with pytest.raises(SecurityError, match="(not accessible|always blocked)"):
            await middleware.execute(
                "search_read",
                {"model": "ir.config_parameter"},
                admin_ctx,
                handler,
            )

    @pytest.mark.asyncio
    async def test_admin_only_model_blocked_non_admin(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        with pytest.raises(SecurityError, match="administrator access"):
            await middleware.execute(
                "search_read",
                {"model": "res.users"},
                user_ctx,
                handler,
            )

    @pytest.mark.asyncio
    async def test_unlisted_model_denied_by_default(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        with pytest.raises(SecurityError, match="not accessible"):
            await middleware.execute(
                "search_read",
                {"model": "unknown.model"},
                user_ctx,
                handler,
            )


# ── Method restriction blocks ──────────────────────────────────────


class TestMethodRestrictionBlocks:
    @pytest.mark.asyncio
    async def test_blocked_method(
        self, middleware: SecurityMiddleware, admin_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        with pytest.raises(SecurityError, match="not allowed"):
            await middleware.execute(
                "execute_method",
                {"model": "sale.order", "method": "sudo"},
                admin_ctx,
                handler,
            )

    @pytest.mark.asyncio
    async def test_unlisted_method_blocked_non_admin(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        # user_ctx doesn't have base.group_system, so execute_method
        # is blocked by RBAC first
        with pytest.raises(SecurityError):
            await middleware.execute(
                "execute_method",
                {"model": "sale.order", "method": "some_method"},
                user_ctx,
                handler,
            )


# ── Field restriction blocks ──────────────────────────────────────


class TestFieldRestrictionBlocks:
    @pytest.mark.asyncio
    async def test_blocked_write_field(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock()
        with pytest.raises(SecurityError, match="never writable"):
            await middleware.execute(
                "update_record",
                {
                    "model": "res.partner",
                    "id": 1,
                    "values": {"password": "new_pass"},
                },
                user_ctx,
                handler,
            )


# ── Response field filtering ───────────────────────────────────────


class TestResponseFieldFiltering:
    @pytest.mark.asyncio
    async def test_sensitive_fields_redacted_in_response(
        self,
        restrictions: RestrictionChecker,
        rbac: RBACManager,
        rate_limiter: RateLimiter,
        audit: AuditLogger,
        sanitizer: ErrorSanitizer,
    ) -> None:
        # We need a model_access config with hr.employee in full_crud
        rc = RestrictionConfig()
        ma = ModelAccessConfig(
            stock_models={"full_crud": ["hr.employee"]},
        )
        checker = RestrictionChecker(rc, ma)
        rbac_cfg = RBACConfig(
            sensitive_fields={
                "hr.employee": {
                    "fields": ["wage"],
                    "required_group": "hr.group_hr_manager",
                },
            },
        )
        rbac_mgr = RBACManager(rbac_cfg, ma)
        mw = SecurityMiddleware(checker, rbac_mgr, rate_limiter, audit, sanitizer)

        handler = AsyncMock(return_value=[{"id": 1, "name": "John", "wage": 5000}])
        ctx = SecurityContext(
            session_id="sess",
            user_id=2,
            user_login="demo",
            user_groups=["base.group_user"],
            is_admin=False,
        )
        result = await mw.execute("search_read", {"model": "hr.employee"}, ctx, handler)
        assert result[0]["wage"] == "***"
        assert result[0]["name"] == "John"


# ── Audit logging ─────────────────────────────────────────────────


class TestAuditLogging:
    @pytest.mark.asyncio
    async def test_success_audited(
        self,
        middleware: SecurityMiddleware,
        user_ctx: SecurityContext,
        tmp_path: Path,
    ) -> None:
        audit = AuditLogger(backend="file", log_path=str(tmp_path / "test.log"))
        middleware._audit = audit

        handler = AsyncMock(return_value=[])
        await middleware.execute(
            "search_read", {"model": "res.partner"}, user_ctx, handler
        )

        log_file = tmp_path / "test.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "search_read" in content
        assert "success" in content

    @pytest.mark.asyncio
    async def test_denied_audited(
        self,
        middleware: SecurityMiddleware,
        user_ctx: SecurityContext,
        tmp_path: Path,
    ) -> None:
        audit = AuditLogger(backend="file", log_path=str(tmp_path / "test.log"))
        middleware._audit = audit

        handler = AsyncMock()
        with pytest.raises(SecurityError):
            await middleware.execute(
                "search_read",
                {"model": "ir.config_parameter"},
                user_ctx,
                handler,
            )

        content = (tmp_path / "test.log").read_text()
        assert "denied" in content


# ── Error sanitization ─────────────────────────────────────────────


class TestErrorSanitization:
    @pytest.mark.asyncio
    async def test_handler_error_sanitized(
        self, middleware: SecurityMiddleware, admin_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock(
            side_effect=RuntimeError("Error at /home/user/odoo/sale.py:42")
        )
        with pytest.raises(SecurityError) as exc_info:
            await middleware.execute(
                "search_read",
                {"model": "res.partner"},
                admin_ctx,
                handler,
            )
        assert "/home/user" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handler_error_audited(
        self,
        middleware: SecurityMiddleware,
        admin_ctx: SecurityContext,
        tmp_path: Path,
    ) -> None:
        audit = AuditLogger(backend="file", log_path=str(tmp_path / "test.log"))
        middleware._audit = audit

        handler = AsyncMock(side_effect=RuntimeError("internal failure"))
        with pytest.raises(SecurityError):
            await middleware.execute(
                "search_read",
                {"model": "res.partner"},
                admin_ctx,
                handler,
            )

        content = (tmp_path / "test.log").read_text()
        assert "error" in content


# ── No model param ─────────────────────────────────────────────────


class TestNoModelParam:
    @pytest.mark.asyncio
    async def test_tool_without_model_passes(
        self, middleware: SecurityMiddleware, user_ctx: SecurityContext
    ) -> None:
        handler = AsyncMock(return_value={"status": "ok"})
        result = await middleware.execute("search_read", {}, user_ctx, handler)
        assert result == {"status": "ok"}


# ── Write value sanitization ──────────────────────────────────────


class TestWriteValueSanitization:
    @pytest.mark.asyncio
    async def test_rbac_removes_blocked_write_fields(
        self,
        restrictions: RestrictionChecker,
        rate_limiter: RateLimiter,
        audit: AuditLogger,
        sanitizer: ErrorSanitizer,
    ) -> None:
        rc = RestrictionConfig()
        ma = ModelAccessConfig(
            stock_models={"full_crud": ["hr.employee"]},
        )
        checker = RestrictionChecker(rc, ma)
        rbac_cfg = RBACConfig(
            sensitive_fields={
                "hr.employee": {
                    "fields": ["wage"],
                    "required_group": "hr.group_hr_manager",
                },
            },
            tool_group_requirements={
                "update_record": ["base.group_user"],
            },
        )
        rbac_mgr = RBACManager(rbac_cfg, ma)
        mw = SecurityMiddleware(checker, rbac_mgr, rate_limiter, audit, sanitizer)

        handler = AsyncMock(return_value={"id": 1})
        ctx = SecurityContext(
            session_id="sess",
            user_id=2,
            user_login="demo",
            user_groups=["base.group_user"],
            is_admin=False,
        )
        await mw.execute(
            "update_record",
            {"model": "hr.employee", "id": 1, "values": {"name": "John", "wage": 9999}},
            ctx,
            handler,
        )
        # The handler should have been called with wage removed
        call_kwargs = handler.call_args[1]
        assert "wage" not in call_kwargs.get("values", {})
        assert call_kwargs["values"]["name"] == "John"
