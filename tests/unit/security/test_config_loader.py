"""Tests for YAML config loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
    _interpolate_env,
    load_config,
)

FIXTURES = Path(__file__).parent / "yaml_fixtures"


# ── RestrictionConfig ──────────────────────────────────────────────


class TestRestrictionConfig:
    def test_load_valid_restrictions(self) -> None:
        data = yaml.safe_load((FIXTURES / "valid_restrictions.yaml").read_text())
        config = RestrictionConfig(**data)
        assert "ir.config_parameter" in config.always_blocked
        assert "res.users" in config.admin_only
        assert "res.company" in config.admin_write_only
        assert "sudo" in config.blocked_methods
        assert "password" in config.blocked_write_fields

    def test_empty_restrictions_defaults(self) -> None:
        config = RestrictionConfig()
        assert config.always_blocked == []
        assert config.admin_only == []
        assert config.admin_write_only == []
        assert config.blocked_methods == []
        assert config.blocked_write_fields == []

    def test_invalid_model_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid model name"):
            RestrictionConfig(always_blocked=["123_bad_name"])

    def test_invalid_model_name_uppercase_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid model name"):
            RestrictionConfig(always_blocked=["UPPERCASE.model"])

    def test_valid_model_names_accepted(self) -> None:
        config = RestrictionConfig(
            always_blocked=["ir.config_parameter", "base.module.update"]
        )
        assert len(config.always_blocked) == 2

    def test_invalid_method_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid method name"):
            RestrictionConfig(blocked_methods=["123bad"])

    def test_valid_method_underscore_prefix(self) -> None:
        config = RestrictionConfig(blocked_methods=["_compute_access"])
        assert "_compute_access" in config.blocked_methods


# ── ModelAccessConfig ──────────────────────────────────────────────


class TestModelAccessConfig:
    def test_load_valid_model_access(self) -> None:
        data = yaml.safe_load((FIXTURES / "valid_model_access.yaml").read_text())
        config = ModelAccessConfig(**data)
        assert config.default_policy == "deny"
        assert "res.partner" in config.stock_models["full_crud"]
        assert "x_custom.model" in config.custom_models["full_crud"]

    def test_default_policy_deny(self) -> None:
        config = ModelAccessConfig()
        assert config.default_policy == "deny"

    def test_invalid_default_policy_raises(self) -> None:
        with pytest.raises(ValueError, match="default_policy must be"):
            ModelAccessConfig(default_policy="maybe")

    def test_contradiction_same_source_raises(self) -> None:
        with pytest.raises(ValueError, match="appears in multiple categories"):
            data = yaml.safe_load((FIXTURES / "contradictory_config.yaml").read_text())
            ModelAccessConfig(**data)

    def test_invalid_model_in_stock_models_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid model name"):
            ModelAccessConfig(stock_models={"full_crud": ["BAD_NAME"]})

    def test_allowed_methods_loaded(self) -> None:
        data = yaml.safe_load((FIXTURES / "valid_model_access.yaml").read_text())
        config = ModelAccessConfig(**data)
        assert "action_confirm" in config.allowed_methods["sale.order"]

    def test_sensitive_fields_loaded(self) -> None:
        data = yaml.safe_load((FIXTURES / "valid_model_access.yaml").read_text())
        config = ModelAccessConfig(**data)
        assert "vat" in config.sensitive_fields["res.partner"]


# ── RBACConfig ─────────────────────────────────────────────────────


class TestRBACConfig:
    def test_load_valid_rbac(self) -> None:
        data = yaml.safe_load((FIXTURES / "valid_rbac.yaml").read_text())
        config = RBACConfig(**data)
        assert "delete_record" in config.tool_group_requirements
        assert "hr.employee" in config.sensitive_fields
        assert "account.move" in config.field_group_overrides

    def test_empty_rbac_defaults(self) -> None:
        config = RBACConfig()
        assert config.tool_group_requirements == {}
        assert config.sensitive_fields == {}
        assert config.field_group_overrides == {}


# ── GatewayConfig ──────────────────────────────────────────────────


class TestGatewayConfig:
    def test_cross_config_contradiction_raises(self) -> None:
        restrictions = RestrictionConfig(always_blocked=["res.partner"])
        model_access = ModelAccessConfig(stock_models={"full_crud": ["res.partner"]})
        with pytest.raises(ValueError, match="always_blocked and full_crud"):
            GatewayConfig(restrictions=restrictions, model_access=model_access)

    def test_no_contradiction_passes(self) -> None:
        restrictions = RestrictionConfig(always_blocked=["ir.cron"])
        model_access = ModelAccessConfig(stock_models={"full_crud": ["res.partner"]})
        config = GatewayConfig(restrictions=restrictions, model_access=model_access)
        assert "ir.cron" in config.restrictions.always_blocked

    def test_default_gateway_config(self) -> None:
        config = GatewayConfig()
        assert config.restrictions.always_blocked == []
        assert config.model_access.default_policy == "deny"


# ── load_config ────────────────────────────────────────────────────


class TestLoadConfig:
    def test_load_from_fixtures_dir(self, tmp_path: Path) -> None:
        # Copy valid fixtures to temp dir with correct names
        for src_name, dst_name in [
            ("valid_restrictions.yaml", "restrictions.yaml"),
            ("valid_rbac.yaml", "rbac.yaml"),
            ("valid_model_access.yaml", "model_access.yaml"),
        ]:
            (tmp_path / dst_name).write_text((FIXTURES / src_name).read_text())
        config = load_config(str(tmp_path))
        assert "ir.config_parameter" in config.restrictions.always_blocked
        assert config.model_access.default_policy == "deny"

    def test_missing_files_use_defaults(self, tmp_path: Path) -> None:
        config = load_config(str(tmp_path))
        assert config.restrictions.always_blocked == []
        assert config.model_access.default_policy == "deny"
        assert config.rbac.tool_group_requirements == {}

    def test_partial_files_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "restrictions.yaml").write_text(
            (FIXTURES / "valid_restrictions.yaml").read_text()
        )
        config = load_config(str(tmp_path))
        assert "ir.config_parameter" in config.restrictions.always_blocked
        assert config.rbac.tool_group_requirements == {}

    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        (tmp_path / "restrictions.yaml").write_text("")
        config = load_config(str(tmp_path))
        assert config.restrictions.always_blocked == []


# ── Environment variable interpolation ─────────────────────────────


class TestEnvInterpolation:
    def test_simple_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "hello")
        assert _interpolate_env("${MY_VAR}") == "hello"

    def test_missing_env_var_becomes_empty(self) -> None:
        # Ensure the var is definitely not set
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        assert _interpolate_env("${NONEXISTENT_VAR_12345}") == ""

    def test_no_interpolation_needed(self) -> None:
        assert _interpolate_env("plain text") == "plain text"

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "foo")
        monkeypatch.setenv("B", "bar")
        assert _interpolate_env("${A}-${B}") == "foo-bar"

    def test_env_interpolation_in_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BLOCKED_MODEL", "ir.cron")
        monkeypatch.setenv("ADMIN_MODEL", "res.users")
        (tmp_path / "restrictions.yaml").write_text(
            (FIXTURES / "env_interpolation.yaml").read_text()
        )
        config = load_config(str(tmp_path))
        assert "ir.cron" in config.restrictions.always_blocked
        assert "res.users" in config.restrictions.admin_only
