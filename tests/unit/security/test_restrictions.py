"""Tests for the restriction checker."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.core.security.restrictions import RestrictionChecker


@pytest.fixture()
def restriction_config() -> RestrictionConfig:
    return RestrictionConfig(
        always_blocked=["ir.config_parameter", "ir.cron", "ir.module.module"],
        admin_only=["res.users", "res.groups"],
        admin_write_only=["res.company", "res.country"],
        blocked_methods=["sudo", "with_user", "unlink_acl"],
        blocked_write_fields=["password", "password_crypt", "groups_id"],
    )


@pytest.fixture()
def model_access_config() -> ModelAccessConfig:
    return ModelAccessConfig(
        default_policy="deny",
        stock_models={
            "full_crud": ["res.partner", "sale.order", "crm.lead"],
            "read_only": ["res.currency", "uom.uom"],
            "admin_only": ["ir.model"],
        },
        custom_models={
            "full_crud": ["x_custom.model"],
            "read_only": ["x_custom.report"],
        },
        allowed_methods={
            "sale.order": ["action_confirm", "action_cancel"],
            "crm.lead": ["action_set_won"],
        },
        sensitive_fields={
            "res.partner": ["vat", "bank_ids"],
        },
    )


@pytest.fixture()
def checker(
    restriction_config: RestrictionConfig,
    model_access_config: ModelAccessConfig,
) -> RestrictionChecker:
    return RestrictionChecker(restriction_config, model_access_config)


# ── check_model_access: always_blocked ─────────────────────────────


class TestAlwaysBlocked:
    def test_blocked_for_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("ir.config_parameter", "read", False)
        assert msg is not None
        assert "always blocked" in msg or "not accessible" in msg

    def test_blocked_for_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("ir.cron", "read", True)
        assert msg is not None
        assert "not accessible" in msg or "always blocked" in msg

    def test_blocked_for_write(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("ir.module.module", "write", True)
        assert msg is not None


# ── check_model_access: admin_only ─────────────────────────────────


class TestAdminOnly:
    def test_blocked_for_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.users", "read", False)
        assert msg is not None
        assert "administrator access" in msg

    def test_allowed_for_admin_read(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.users", "read", True)
        assert msg is None

    def test_allowed_for_admin_write(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.users", "write", True)
        assert msg is None


# ── check_model_access: admin_write_only ───────────────────────────


class TestAdminWriteOnly:
    def test_read_allowed_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.company", "read", False)
        assert msg is None

    def test_write_blocked_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.company", "write", False)
        assert msg is not None
        assert "Write access" in msg

    def test_create_blocked_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.country", "create", False)
        assert msg is not None
        assert "Write access" in msg

    def test_delete_blocked_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.company", "delete", False)
        assert msg is not None

    def test_write_allowed_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.company", "write", True)
        assert msg is None


# ── check_model_access: full_crud ──────────────────────────────────


class TestFullCrud:
    def test_read_allowed(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.partner", "read", False)
        assert msg is None

    def test_write_allowed(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("sale.order", "write", False)
        assert msg is None

    def test_create_allowed(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("crm.lead", "create", False)
        assert msg is None

    def test_delete_allowed(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.partner", "delete", False)
        assert msg is None

    def test_custom_model_full_crud(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("x_custom.model", "write", False)
        assert msg is None


# ── check_model_access: read_only ──────────────────────────────────


class TestReadOnly:
    def test_read_allowed(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.currency", "read", False)
        assert msg is None

    def test_write_blocked(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.currency", "write", False)
        assert msg is not None
        assert "read-only" in msg

    def test_create_blocked(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("uom.uom", "create", False)
        assert msg is not None

    def test_write_blocked_even_for_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("res.currency", "write", True)
        assert msg is not None
        assert "read-only" in msg

    def test_custom_model_read_only(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("x_custom.report", "read", False)
        assert msg is None

    def test_custom_model_write_blocked(
        self,
        checker: RestrictionChecker,
    ) -> None:
        msg = checker.check_model_access("x_custom.report", "write", False)
        assert msg is not None


# ── check_model_access: default policy ─────────────────────────────


class TestDefaultPolicy:
    def test_deny_unlisted_model_non_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_model_access("unknown.model", "read", False)
        assert msg is not None
        assert "not accessible" in msg

    def test_deny_unlisted_model_admin_allowed(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_model_access("unknown.model", "read", True)
        assert msg is None

    def test_allow_policy_unlisted_model_readable(self) -> None:
        config = RestrictionConfig()
        ma = ModelAccessConfig(default_policy="allow")
        ch = RestrictionChecker(config, ma)
        msg = ch.check_model_access("any.model", "read", False)
        assert msg is None

    def test_allow_policy_unlisted_model_write_blocked_non_admin(self) -> None:
        config = RestrictionConfig()
        ma = ModelAccessConfig(default_policy="allow")
        ch = RestrictionChecker(config, ma)
        msg = ch.check_model_access("any.model", "write", False)
        assert msg is not None
        assert "read-only" in msg

    def test_allow_policy_admin_can_write(self) -> None:
        config = RestrictionConfig()
        ma = ModelAccessConfig(default_policy="allow")
        ch = RestrictionChecker(config, ma)
        msg = ch.check_model_access("any.model", "write", True)
        assert msg is None


# ── check_model_access: model_access admin_only ────────────────────


class TestModelAccessAdminOnly:
    def test_admin_only_in_model_access_blocks_non_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_model_access("ir.model", "read", False)
        assert msg is not None
        assert "administrator access" in msg

    def test_admin_only_in_model_access_allows_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_model_access("ir.model", "read", True)
        assert msg is None


# ── check_method_access ────────────────────────────────────────────


class TestMethodAccess:
    def test_blocked_method_always_blocked(self, checker: RestrictionChecker) -> None:
        msg = checker.check_method_access("res.partner", "sudo", False)
        assert msg is not None
        assert "not allowed" in msg

    def test_blocked_method_blocked_for_admin_too(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_method_access("res.partner", "sudo", True)
        assert msg is not None

    def test_private_method_blocked_non_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_method_access("sale.order", "_private", False)
        assert msg is not None
        assert "Private methods" in msg

    def test_private_method_allowed_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_method_access("sale.order", "_private", True)
        assert msg is None

    def test_allowed_method_passes(self, checker: RestrictionChecker) -> None:
        msg = checker.check_method_access("sale.order", "action_confirm", False)
        assert msg is None

    def test_unlisted_method_blocked_non_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_method_access("sale.order", "do_something", False)
        assert msg is not None
        assert "not in the allowed list" in msg

    def test_unlisted_method_allowed_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_method_access("sale.order", "do_something", True)
        assert msg is None

    def test_model_with_no_allowed_methods_non_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_method_access("res.partner", "some_method", False)
        assert msg is not None
        assert "No methods are configured" in msg

    def test_model_with_no_allowed_methods_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_method_access("res.partner", "some_method", True)
        assert msg is None


# ── check_field_write ──────────────────────────────────────────────


class TestFieldWrite:
    def test_blocked_field_always_blocked(self, checker: RestrictionChecker) -> None:
        msg = checker.check_field_write("res.users", "password", False)
        assert msg is not None
        assert "never writable" in msg

    def test_blocked_field_blocked_for_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_field_write("res.users", "password", True)
        assert msg is not None

    def test_sensitive_field_blocked_non_admin(
        self, checker: RestrictionChecker
    ) -> None:
        msg = checker.check_field_write("res.partner", "vat", False)
        assert msg is not None
        assert "administrator access" in msg

    def test_sensitive_field_allowed_admin(self, checker: RestrictionChecker) -> None:
        msg = checker.check_field_write("res.partner", "vat", True)
        assert msg is None

    def test_normal_field_allowed(self, checker: RestrictionChecker) -> None:
        msg = checker.check_field_write("res.partner", "name", False)
        assert msg is None


# ── get_accessible_models ──────────────────────────────────────────


class TestGetAccessibleModels:
    def test_non_admin_sees_public_models(self, checker: RestrictionChecker) -> None:
        models = checker.get_accessible_models(False)
        assert "res.partner" in models
        assert "sale.order" in models
        assert "res.currency" in models
        assert "x_custom.model" in models
        assert "x_custom.report" in models

    def test_non_admin_does_not_see_admin_only(
        self, checker: RestrictionChecker
    ) -> None:
        models = checker.get_accessible_models(False)
        assert "ir.model" not in models

    def test_admin_sees_admin_only(self, checker: RestrictionChecker) -> None:
        models = checker.get_accessible_models(True)
        assert "ir.model" in models
        assert "res.users" in models

    def test_always_blocked_never_in_list(self, checker: RestrictionChecker) -> None:
        models = checker.get_accessible_models(True)
        assert "ir.config_parameter" not in models
        assert "ir.cron" not in models

    def test_admin_write_only_visible_to_all(self, checker: RestrictionChecker) -> None:
        models = checker.get_accessible_models(False)
        assert "res.company" in models
        assert "res.country" in models

    def test_returned_list_is_sorted(self, checker: RestrictionChecker) -> None:
        models = checker.get_accessible_models(False)
        assert models == sorted(models)


# ── _ALWAYS_BLOCKED_METHODS (hardcoded, not configurable) ─────────


class TestAlwaysBlockedMethods:
    """Verify that the hardcoded _ALWAYS_BLOCKED_METHODS set blocks
    dangerous Odoo ORM methods for ALL users, including admin.

    These methods (sudo, with_user, etc.) can escalate privileges or
    bypass security and must never be callable through the gateway.
    """

    def test_sudo_blocked_even_for_admin(self, checker: RestrictionChecker) -> None:
        result = checker.check_method_access("res.partner", "sudo", is_admin=True)
        assert result is not None
        assert "not allowed" in result

    def test_with_user_blocked_even_for_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_method_access("res.partner", "with_user", is_admin=True)
        assert result is not None
        assert "not allowed" in result

    def test_with_company_blocked_even_for_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_method_access(
            "res.partner", "with_company", is_admin=True
        )
        assert result is not None
        assert "not allowed" in result

    def test_with_context_blocked_even_for_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_method_access(
            "res.partner", "with_context", is_admin=True
        )
        assert result is not None
        assert "not allowed" in result

    def test_with_env_blocked_even_for_admin(self, checker: RestrictionChecker) -> None:
        result = checker.check_method_access("res.partner", "with_env", is_admin=True)
        assert result is not None
        assert "not allowed" in result

    def test_with_prefetch_blocked_even_for_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_method_access(
            "res.partner", "with_prefetch", is_admin=True
        )
        assert result is not None
        assert "not allowed" in result

    def test_auto_init_blocked_even_for_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_method_access("res.partner", "_auto_init", is_admin=True)
        assert result is not None
        assert "not allowed" in result

    def test_sql_blocked_even_for_admin(self, checker: RestrictionChecker) -> None:
        result = checker.check_method_access("res.partner", "_sql", is_admin=True)
        assert result is not None
        assert "not allowed" in result

    def test_sudo_blocked_for_non_admin(self, checker: RestrictionChecker) -> None:
        result = checker.check_method_access("res.partner", "sudo", is_admin=False)
        assert result is not None

    def test_with_user_blocked_for_non_admin(self, checker: RestrictionChecker) -> None:
        result = checker.check_method_access("res.partner", "with_user", is_admin=False)
        assert result is not None

    def test_blocked_methods_match_source_constant(self) -> None:
        """Verify the hardcoded _ALWAYS_BLOCKED_METHODS contains critical methods."""
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_METHODS

        # Core privilege-escalation methods that must always be present
        required = {
            "sudo",
            "with_user",
            "with_company",
            "with_context",
            "with_env",
            "with_prefetch",
            "_auto_init",
            "_sql",
        }
        assert required.issubset(_ALWAYS_BLOCKED_METHODS), (
            f"Missing required methods: {required - _ALWAYS_BLOCKED_METHODS}"
        )
        # All entries must be non-empty strings
        for m in _ALWAYS_BLOCKED_METHODS:
            assert isinstance(m, str) and len(m) > 0

    def test_always_blocked_methods_on_any_model(
        self, checker: RestrictionChecker
    ) -> None:
        """These methods should be blocked regardless of the model."""
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_METHODS

        for method in _ALWAYS_BLOCKED_METHODS:
            for model in ["res.partner", "sale.order", "ir.model"]:
                result = checker.check_method_access(model, method, is_admin=True)
                assert result is not None, (
                    f"{method} on {model} should be blocked even for admin"
                )


# ── always_blocked models: ensure config-driven list also works ───


class TestAlwaysBlockedModelsHardened:
    """Additional tests verifying always_blocked models are truly inaccessible
    for all operations and all user types.
    """

    def test_ir_config_parameter_blocked_read_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_model_access(
            "ir.config_parameter", "read", is_admin=True
        )
        assert result is not None
        assert "always blocked" in result or "not accessible" in result

    def test_ir_config_parameter_blocked_write_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_model_access(
            "ir.config_parameter", "write", is_admin=True
        )
        assert result is not None

    def test_ir_config_parameter_blocked_create_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_model_access(
            "ir.config_parameter", "create", is_admin=True
        )
        assert result is not None

    def test_ir_config_parameter_blocked_delete_admin(
        self, checker: RestrictionChecker
    ) -> None:
        result = checker.check_model_access(
            "ir.config_parameter", "delete", is_admin=True
        )
        assert result is not None

    def test_ir_cron_blocked_all_operations_admin(
        self, checker: RestrictionChecker
    ) -> None:
        for op in ["read", "create", "write", "delete"]:
            result = checker.check_model_access("ir.cron", op, is_admin=True)
            assert result is not None, (
                f"ir.cron should be blocked for {op} even for admin"
            )

    def test_ir_module_module_blocked_all_operations_admin(
        self, checker: RestrictionChecker
    ) -> None:
        for op in ["read", "create", "write", "delete"]:
            result = checker.check_model_access("ir.module.module", op, is_admin=True)
            assert result is not None, (
                f"ir.module.module should be blocked for {op} even for admin"
            )

    def test_blocked_models_not_in_accessible_list(
        self, checker: RestrictionChecker
    ) -> None:
        """Always-blocked models should never appear in accessible model list."""
        admin_models = checker.get_accessible_models(True)
        non_admin_models = checker.get_accessible_models(False)

        for blocked in ["ir.config_parameter", "ir.cron", "ir.module.module"]:
            assert blocked not in admin_models
            assert blocked not in non_admin_models

    def test_hardcoded_blocked_models_rejected_even_in_full_crud(self) -> None:
        """Hardcoded _ALWAYS_BLOCKED_MODELS must be rejected even if in full_crud."""
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_MODELS

        # Try to grant full_crud to hardcoded blocked models
        blocked_list = list(_ALWAYS_BLOCKED_MODELS)
        config = RestrictionConfig()
        ma = ModelAccessConfig(
            default_policy="allow",
            stock_models={"full_crud": blocked_list},
        )
        ch = RestrictionChecker(config, ma)

        for model in _ALWAYS_BLOCKED_MODELS:
            result = ch.check_model_access(model, "read", is_admin=True)
            assert result is not None, (
                f"Hardcoded blocked model '{model}' should be blocked "
                f"even when placed in full_crud"
            )


# ── _ALWAYS_BLOCKED_MODELS (hardcoded, not configurable) ─────────


class TestHardcodedAlwaysBlockedModels:
    """Verify the hardcoded _ALWAYS_BLOCKED_MODELS set blocks sensitive models
    regardless of YAML config and admin status.
    """

    def test_hardcoded_set_contains_critical_models(self) -> None:
        """The hardcoded set should contain known sensitive models."""
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_MODELS

        # Core security-critical models that must always be present
        required = {
            "ir.config_parameter",
            "res.users.apikeys",
            "change.password.wizard",
            "change.password.user",
            "ir.actions.server",
        }
        assert required.issubset(_ALWAYS_BLOCKED_MODELS), (
            f"Missing required models: {required - _ALWAYS_BLOCKED_MODELS}"
        )

    def test_hardcoded_blocked_model_rejected_for_admin(self) -> None:
        """Hardcoded blocked models should be blocked even for admin."""
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_MODELS

        # Create a minimal checker with NO config-based blocking
        config = RestrictionConfig()
        ma = ModelAccessConfig(default_policy="allow")
        ch = RestrictionChecker(config, ma)

        for model in _ALWAYS_BLOCKED_MODELS:
            for op in ["read", "create", "write", "delete"]:
                result = ch.check_model_access(model, op, is_admin=True)
                assert result is not None, (
                    f"Hardcoded blocked model '{model}' should be blocked "
                    f"for {op} even for admin with no config restrictions"
                )

    def test_hardcoded_blocked_model_rejected_for_non_admin(self) -> None:
        """Hardcoded blocked models should be blocked for non-admin users."""
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_MODELS

        config = RestrictionConfig()
        ma = ModelAccessConfig(default_policy="allow")
        ch = RestrictionChecker(config, ma)

        for model in _ALWAYS_BLOCKED_MODELS:
            result = ch.check_model_access(model, "read", is_admin=False)
            assert result is not None, (
                f"Hardcoded blocked model '{model}' should be blocked for non-admin"
            )

    def test_hardcoded_blocked_overrides_full_crud_config(self) -> None:
        """Even if a model is in full_crud config, hardcoded blocking wins.

        Note: GatewayConfig cross-validation prevents this in production,
        but we test the RestrictionChecker alone to verify defense in depth.
        """
        from odoo_mcp_gateway.core.security.restrictions import _ALWAYS_BLOCKED_MODELS

        # Pick one hardcoded model to try adding to full_crud
        model = next(iter(_ALWAYS_BLOCKED_MODELS))

        config = RestrictionConfig()
        # We cannot create a GatewayConfig with contradictions,
        # but we can test RestrictionChecker directly
        ma = ModelAccessConfig(
            default_policy="allow",
            stock_models={"full_crud": [model]},
        )
        ch = RestrictionChecker(config, ma)

        result = ch.check_model_access(model, "read", is_admin=True)
        assert result is not None, (
            f"Hardcoded model '{model}' should be blocked even if in full_crud"
        )
