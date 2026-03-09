"""Load and validate YAML security configuration files."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator, model_validator

_MODEL_PATTERN = re.compile(r"^[a-z][a-z0-9_.]*$")
_METHOD_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


class RestrictionConfig(BaseModel):
    """Hard-coded safety guardrails applied before Odoo's own access rules."""

    always_blocked: list[str] = []
    admin_only: list[str] = []
    admin_write_only: list[str] = []
    blocked_methods: list[str] = []
    blocked_write_fields: list[str] = []

    @field_validator("always_blocked", "admin_only", "admin_write_only")
    @classmethod
    def validate_model_names(cls, v: list[str]) -> list[str]:
        for name in v:
            # Strip inline comments for validation
            clean = name.split("#")[0].strip() if "#" in name else name.strip()
            if not _MODEL_PATTERN.match(clean):
                raise ValueError(
                    f"Invalid model name '{clean}': must match [a-z][a-z0-9_.]*"
                )
        return [n.split("#")[0].strip() if "#" in n else n.strip() for n in v]

    @field_validator("blocked_methods")
    @classmethod
    def validate_method_names(cls, v: list[str]) -> list[str]:
        for name in v:
            clean = name.split("#")[0].strip() if "#" in name else name.strip()
            if not _METHOD_PATTERN.match(clean):
                raise ValueError(
                    f"Invalid method name '{clean}': must match [a-zA-Z_][a-zA-Z0-9_]*"
                )
        return [n.split("#")[0].strip() if "#" in n else n.strip() for n in v]


class RBACConfig(BaseModel):
    """Role-based access control overlays mapping tools to Odoo groups."""

    tool_group_requirements: dict[str, list[str]] = {}
    sensitive_fields: dict[str, Any] = {}
    field_group_overrides: dict[str, Any] = {}


class ModelAccessConfig(BaseModel):
    """Declarative model allow-list controlling which models the gateway exposes."""

    default_policy: str = "deny"
    stock_models: dict[str, list[str]] = {}
    custom_models: dict[str, list[str]] = {}
    allowed_methods: dict[str, list[str]] = {}
    sensitive_fields: dict[str, list[str]] = {}

    @field_validator("default_policy")
    @classmethod
    def validate_default_policy(cls, v: str) -> str:
        if v not in ("deny", "allow"):
            raise ValueError(f"default_policy must be 'deny' or 'allow', got '{v}'")
        return v

    @field_validator("stock_models", "custom_models")
    @classmethod
    def validate_model_access_names(
        cls, v: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        for _category, models in v.items():
            for name in models:
                clean = name.split("#")[0].strip() if "#" in name else name.strip()
                if clean and not _MODEL_PATTERN.match(clean):
                    raise ValueError(
                        f"Invalid model name '{clean}': must match [a-z][a-z0-9_.]*"
                    )
        return v

    @model_validator(mode="after")
    def check_no_contradictions(self) -> ModelAccessConfig:
        """Ensure no model appears in contradictory categories."""
        all_models: dict[str, list[str]] = {}
        for source_name, source in [
            ("stock_models", self.stock_models),
            ("custom_models", self.custom_models),
        ]:
            for category, models in source.items():
                for model in models:
                    if not model:
                        continue
                    key = f"{source_name}.{category}"
                    if model not in all_models:
                        all_models[model] = []
                    all_models[model].append(key)

        for model, categories in all_models.items():
            if len(categories) > 1:
                raise ValueError(
                    f"Model '{model}' appears in multiple categories: "
                    f"{', '.join(categories)}"
                )
        return self


class GatewayConfig(BaseModel):
    """Merged gateway security configuration."""

    restrictions: RestrictionConfig = RestrictionConfig()
    rbac: RBACConfig = RBACConfig()
    model_access: ModelAccessConfig = ModelAccessConfig()

    @model_validator(mode="after")
    def check_cross_config_contradictions(self) -> GatewayConfig:
        """Ensure no model is both always_blocked and in full_crud."""
        blocked = set(self.restrictions.always_blocked)
        for source in [self.model_access.stock_models, self.model_access.custom_models]:
            full_crud = set(source.get("full_crud", []))
            overlap = blocked & full_crud
            if overlap:
                raise ValueError(
                    "Models cannot be both always_blocked and full_crud: "
                    f"{', '.join(sorted(overlap))}"
                )
        return self


def _interpolate_env(value: str) -> str:
    """Replace ${ENV_VAR} with environment variable values."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _interpolate_recursive(data: Any) -> Any:
    """Recursively interpolate environment variables in a data structure."""
    if isinstance(data, str):
        return _interpolate_env(data)
    if isinstance(data, list):
        return [_interpolate_recursive(item) for item in data]
    if isinstance(data, dict):
        return {k: _interpolate_recursive(v) for k, v in data.items()}
    return data


_log = logging.getLogger(__name__)


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a single YAML file, returning empty dict if missing."""
    if not path.exists():
        _log.warning(
            "Config file %s not found — using defaults. "
            "Copy the .example file to enable configuration.",
            path,
        )
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}, got {type(data).__name__}")
    result: dict[str, Any] = _interpolate_recursive(data)
    return result


def load_config(config_dir: str) -> GatewayConfig:
    """Load and merge all YAML configs from directory.

    Looks for restrictions.yaml, rbac.yaml, model_access.yaml.
    Missing files are handled gracefully with defaults.
    """
    base = Path(config_dir)

    restrictions_data = _load_yaml_file(base / "restrictions.yaml")
    rbac_data = _load_yaml_file(base / "rbac.yaml")
    model_access_data = _load_yaml_file(base / "model_access.yaml")

    restrictions = RestrictionConfig(**restrictions_data)
    rbac = RBACConfig(**rbac_data)
    model_access = ModelAccessConfig(**model_access_data)

    return GatewayConfig(
        restrictions=restrictions,
        rbac=rbac,
        model_access=model_access,
    )
