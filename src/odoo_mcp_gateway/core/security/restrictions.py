"""Restriction checker: enforces hard-coded safety guardrails."""

from __future__ import annotations

from .config_loader import ModelAccessConfig, RestrictionConfig

# Non-configurable set of always-blocked dangerous methods.
# These cannot be overridden by YAML config and are blocked for ALL users
# including admin.
_ALWAYS_BLOCKED_METHODS: frozenset[str] = frozenset(
    {
        "sudo",
        "with_user",
        "with_company",
        "with_context",
        "with_env",
        "with_prefetch",
        "_auto_init",
        "_sql",
        "_register_hook",
        "_write",
        "_create",
        "_read",
        "_setup_base",
        "_setup_fields",
        "_setup_complete",
        "init",
        "_table_query",
        "_read_group_raw",
    }
)

# Non-configurable set of always-blocked models.
# These contain security-critical data and must NEVER be exposed through the
# gateway regardless of YAML config or admin status.
_ALWAYS_BLOCKED_MODELS: frozenset[str] = frozenset(
    {
        "ir.config_parameter",
        "res.users.apikeys",
        "change.password.wizard",
        "change.password.user",
        "ir.actions.server",
        "ir.cron",
        "ir.module.module",
        "ir.model.access",
        "ir.rule",
        "ir.mail_server",
        "ir.ui.view",
        "base.module.update",
        "base.module.upgrade",
        "base.module.uninstall",
        "res.config.settings",
        "fetchmail.server",
        "bus.bus",
    }
)


class RestrictionChecker:
    """Enforces model, method, and field restrictions before Odoo's own ACLs.

    All lookup structures are sets for O(1) membership checks.
    """

    def __init__(
        self, config: RestrictionConfig, model_access: ModelAccessConfig
    ) -> None:
        self._always_blocked: set[str] = set(config.always_blocked)
        self._admin_only: set[str] = set(config.admin_only)
        self._admin_write_only: set[str] = set(config.admin_write_only)
        self._blocked_methods: set[str] = set(config.blocked_methods)
        self._blocked_write_fields: set[str] = set(config.blocked_write_fields)
        self._model_access = model_access

        # Build merged model access sets from stock + custom models
        self._full_crud: set[str] = set()
        self._read_only: set[str] = set()
        self._access_admin_only: set[str] = set()

        for source in [model_access.stock_models, model_access.custom_models]:
            self._full_crud.update(source.get("full_crud", []))
            self._read_only.update(source.get("read_only", []))
            self._access_admin_only.update(source.get("admin_only", []))

        self._allowed_methods = model_access.allowed_methods
        self._default_policy = model_access.default_policy
        self._sensitive_fields = model_access.sensitive_fields

    def check_model_access(
        self, model: str, operation: str, is_admin: bool
    ) -> str | None:
        """Check if the model/operation is allowed.

        Returns error message if denied, None if allowed.
        operation: 'read', 'create', 'write', 'delete'
        """
        # 0. Hard-coded always-blocked models (cannot be overridden, blocks everyone)
        if model in _ALWAYS_BLOCKED_MODELS:
            return f"Access denied: '{model}' is always blocked"

        # 1. always_blocked -> denied for everyone
        if model in self._always_blocked:
            return f"Model '{model}' is not accessible through the gateway"

        # 2. admin_only restriction -> non-admin blocked
        if model in self._admin_only and not is_admin:
            return f"Model '{model}' requires administrator access"

        # 3. admin_write_only: read OK for all, write requires admin
        write_ops = {"create", "write", "delete"}
        if model in self._admin_write_only:
            if operation in write_ops and not is_admin:
                return f"Write access to '{model}' requires administrator"
            return None

        # 4. Check model_access config categories
        if model in self._access_admin_only:
            if not is_admin:
                return f"Model '{model}' requires administrator access"
            return None

        if model in self._full_crud:
            return None

        if model in self._read_only:
            if operation in write_ops:
                return f"Model '{model}' is read-only"
            return None

        # 5. Not in any list -> apply default_policy
        if self._default_policy == "deny":
            if is_admin:
                return None
            return f"Model '{model}' is not accessible through the gateway"

        # default_policy == "allow" -> treat as read_only for non-admin
        if operation in write_ops and not is_admin:
            return f"Model '{model}' is read-only"
        return None

    def check_method_access(
        self, model: str, method: str, is_admin: bool
    ) -> str | None:
        """Check if a method call is allowed.

        Returns error message if blocked, None if allowed.
        """
        # 0. Hard-coded always-blocked methods (cannot be overridden, blocks everyone)
        if method in _ALWAYS_BLOCKED_METHODS:
            return f"Method '{method}' is not allowed through the gateway"

        # 1. blocked_methods from config -> always blocked
        if method in self._blocked_methods:
            return f"Method '{method}' is not allowed through the gateway"

        # 2. Private methods (underscore prefix) -> admin only
        if method.startswith("_") and not is_admin:
            return "Private methods require administrator access"

        # 3. Check allowed_methods for the model
        if model in self._allowed_methods:
            if method not in self._allowed_methods[model]:
                if not is_admin:
                    return (
                        f"Method '{method}' is not in the allowed list "
                        f"for model '{model}'"
                    )
        elif not is_admin:
            # Model has no allowed_methods entry -> block non-admin
            return f"No methods are configured as allowed for model '{model}'"

        return None

    def check_field_write(self, model: str, field: str, is_admin: bool) -> str | None:
        """Check if a field write is allowed.

        Returns error message if blocked, None if allowed.
        """
        if field in self._blocked_write_fields:
            return f"Field '{field}' is never writable through the gateway"

        # Check sensitive_fields from model_access
        if model in self._sensitive_fields:
            if field in self._sensitive_fields[model] and not is_admin:
                return (
                    f"Field '{field}' on model '{model}' requires "
                    "administrator access to write"
                )

        return None

    def get_accessible_models(self, is_admin: bool) -> list[str]:
        """Return sorted list of models the user can access."""
        models: set[str] = set()

        # full_crud is accessible to everyone
        models.update(self._full_crud)

        # read_only is accessible to everyone (for reading)
        models.update(self._read_only)

        # admin_write_only models are readable by everyone
        models.update(self._admin_write_only)

        if is_admin:
            # Admin can also access admin_only models
            models.update(self._admin_only)
            models.update(self._access_admin_only)

        # Remove always_blocked (YAML + hardcoded)
        models -= self._always_blocked
        models -= _ALWAYS_BLOCKED_MODELS

        return sorted(models)
