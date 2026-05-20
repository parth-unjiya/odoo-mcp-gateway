"""Load and validate YAML security configuration files."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator, model_validator

# Match canonical Odoo model identifiers: segments separated by single
# dots, each segment lowercase alnum/underscore. Previously the looser
# ``[a-z][a-z0-9_.]*`` pattern accepted ``res.partner.`` (trailing dot)
# and ``res..partner`` (empty segment) — both would silently desync
# the blocked-model set from the real catalog.
_MODEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
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
    # UAT M1 / MED-2 (Odoo 18 + 19): portal users see a much smaller
    # curated set via ``list_models``. When unset, the default is the
    # safest tight allow-list (``res.partner`` only). The values listed
    # here are intersected with the already-accessible model list — a
    # model not in the user's RBAC scope cannot appear here. This is
    # a UX correction, not a security control: Odoo's row-level ACL
    # already clamps results, but listing 35+ model names a portal
    # user cannot actually read overstates the attack surface.
    portal_models: list[str] = []

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

    @field_validator("portal_models")
    @classmethod
    def validate_portal_model_names(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for name in v:
            clean = name.split("#")[0].strip() if "#" in name else name.strip()
            if not clean:
                continue
            if not _MODEL_PATTERN.match(clean):
                raise ValueError(
                    f"Invalid model name '{clean}': must match [a-z][a-z0-9_.]*"
                )
            cleaned.append(clean)
        return cleaned

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

    If ``config_dir`` does not exist OR contains none of the expected
    files, a fallback attempt is made on the current working directory
    (so users who didn't set ``CONFIG_DIR`` still pick up YAML files
    sitting alongside their command-line invocation).
    """
    base = Path(config_dir)
    candidate_files = ("restrictions.yaml", "rbac.yaml", "model_access.yaml")

    def _any_yaml_present(p: Path) -> bool:
        return any((p / name).exists() for name in candidate_files)

    if not base.exists() or not _any_yaml_present(base):
        # Try CWD as a fallback before falling through to defaults.
        cwd_fallback = Path(".")
        if cwd_fallback.exists() and _any_yaml_present(cwd_fallback):
            _log.info(
                "No YAML configs in %s; falling back to current directory",
                base,
            )
            base = cwd_fallback

    _log.info(
        "Loading gateway config from %s (exists=%s)",
        base.resolve(),
        base.exists(),
    )

    restrictions_data = _load_yaml_file(base / "restrictions.yaml")
    rbac_data = _load_yaml_file(base / "rbac.yaml")
    model_access_data = _load_yaml_file(base / "model_access.yaml")

    loaded = [name for name in candidate_files if (base / name).exists()]
    if loaded:
        _log.info("Loaded YAML config files: %s", ", ".join(loaded))
    else:
        _log.warning(
            "No YAML config files found in %s — gateway runs with "
            "hardcoded defaults only (default_policy=deny). To customise, "
            "copy config/*.yaml.example to config/*.yaml and set "
            "CONFIG_DIR if needed.",
            base.resolve(),
        )

    restrictions = RestrictionConfig(**restrictions_data)
    rbac = RBACConfig(**rbac_data)
    model_access = ModelAccessConfig(**model_access_data)

    return GatewayConfig(
        restrictions=restrictions,
        rbac=rbac,
        model_access=model_access,
    )
