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
        # Bulk import/export — bypass field-level RBAC and validations
        "name_create",  # creates records bypassing many validations
        "load",  # bulk CSV-like load that can write any field
        "import_data",  # bulk import path bypassing ORM guards
        "export_data",  # bulk export path bypassing field RBAC
        # Cache poisoning vectors (Odoo 16+)
        "flush_recordset",  # forces cache flush, can affect concurrent ops
        "invalidate_recordset",  # cache invalidation; reachable side-effects
        # Internal search-panel helpers (Odoo 14+) — leak data via aggregates
        "_search_panel_select_range",
        "_search_panel_select_multi_range",
        "_search_panel_domain_image",
        # Underscored search/aggregation internals
        "_search",  # internal search bypassing public search filters
        "_read_progress_bar",  # progress-bar aggregation can leak counts
    }
)

# Non-configurable set of always-blocked models.
# These contain security-critical data and must NEVER be exposed through the
# gateway regardless of YAML config or admin status.
_ALWAYS_BLOCKED_MODELS: frozenset[str] = frozenset(
    {
        "ir.config_parameter",
        "ir.attachment",  # SSRF risk via file:// URLs, path traversal
        "res.users",
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
        "base.automation",  # can trigger server actions (arbitrary code)
        "res.config.settings",
        "fetchmail.server",
        "bus.bus",
        "mail.mail",
        "mail.template",  # email templates with portal URLs (phishing enabler)
        "payment.token",  # stored credit cards / payment methods
        "payment.provider",  # payment gateway API keys and secrets
        # 2FA / TOTP enrollment — attacker could enroll their own TOTP device
        "auth.totp.wizard",
        "auth.totp.device",
        # Login history / security telemetry
        "res.users.log",  # login timestamps, IPs (security telemetry leak)
        "ir.logging",  # server log entries (stack traces, SQL fragments)
        # IAP (In-App Purchase) tokens / Odoo cloud services
        "iap.account",  # IAP API tokens with credit balances
        # Bulk data export models
        "ir.exports",  # saved export configurations (data exfil vector)
        "ir.exports.line",  # column definitions for ir.exports
        # Bulk system mailings
        "digest.digest",  # automated digest mailings (abuse / spoof vector)
        # Metamodel — exposing ir.model lets non-admins enumerate every
        # installed model (including internal/custom ones) and craft
        # targeted probes against models they should not even know about.
        # The dedicated ``list_models`` tool exposes a curated subset
        # through the model_registry, which does its own admin-context
        # query at startup; direct CRUD on ir.model is never appropriate.
        "ir.model",
        "ir.model.fields",
    }
)

# Non-configurable set of models that are always read-only through the gateway.
# Reads are allowed (Odoo's ir.rule enforces per-user record access),
# but creates/writes/deletes are blocked to prevent message injection,
# fake follower manipulation, and activity spoofing.
_ALWAYS_READ_ONLY_MODELS: frozenset[str] = frozenset(
    {
        "mail.message",
        "mail.followers",
        "mail.activity",
        "discuss.channel",
        # Notification model — writing here enables notification spoofing
        "mail.notification",
        # Email composition wizard — abuse vector for sending arbitrary emails
        "mail.compose.message",
        # Email forwarding aliases — hijack vector (redirect inbound mail)
        "mail.alias",
        # Channel membership manipulation (Odoo 17+ name)
        "discuss.channel.member",
    }
)


# Non-configurable set of fields that must never be writable through the
# gateway.  These protect against privilege escalation and account takeover
# even when no YAML config is deployed.
_ALWAYS_BLOCKED_WRITE_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "password_crypt",
        "groups_id",
        "totp_secret",
        "signup_token",
        "signup_type",
        "signup_expiration",
        "api_key",
        "share",
        "active",
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
        self._blocked_write_fields: set[str] = (
            set(config.blocked_write_fields) | _ALWAYS_BLOCKED_WRITE_FIELDS
        )
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

        # 0a. Hard-coded read-only models — reads OK, writes blocked for everyone
        if model in _ALWAYS_READ_ONLY_MODELS:
            if operation in {"create", "write", "delete"}:
                return (
                    f"Access denied: '{model}' is read-only through the gateway. "
                    "Use workflow actions (e.g., action_quotation_send) to send "
                    "messages through proper Odoo channels."
                )
            return None

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

        # 2. Private methods (underscore prefix) — blocked for EVERYONE unless
        # explicitly whitelisted per-model. Admin should not be a wildcard for
        # internal methods (e.g. _compute_*, _inverse_*, _constrains_*, custom
        # _unsafe_helper) since these are not part of the public API and may
        # bypass business-logic guards.
        if method.startswith("_"):
            allowed = self._allowed_methods.get(model, [])
            if method not in allowed:
                return (
                    f"Private method '{method}' is not whitelisted for model "
                    f"'{model}'. Internal Odoo methods cannot be called via "
                    "the gateway unless explicitly listed in "
                    "model_access.yaml allowed_methods."
                )
            # Falls through to step 3 — explicit whitelist already authorises.

        # 3. Check allowed_methods for the model (for non-admin users)
        if model in self._allowed_methods:
            if method not in self._allowed_methods[model] and not is_admin:
                return (
                    f"Method '{method}' is not in the allowed list for model '{model}'"
                )
        elif not is_admin and not method.startswith("_"):
            # Public method on a model with no allowed_methods entry — block
            # non-admin. Private methods already passed the explicit-whitelist
            # check above (they reach this branch only if whitelisted).
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
