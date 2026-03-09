"""Model registry: discovers and classifies Odoo models."""

from __future__ import annotations

from typing import Any

from .models import AccessLevel, ModelInfo

# Known stock Odoo module prefixes
STOCK_MODULE_PREFIXES = frozenset(
    {
        "base",
        "web",
        "bus",
        "sale",
        "purchase",
        "stock",
        "account",
        "hr",
        "project",
        "crm",
        "mail",
        "calendar",
        "website",
        "mrp",
        "fleet",
        "maintenance",
        "helpdesk",
        "l10n_",
        "auth_",
        "board",
        "contacts",
        "digest",
        "event",
        "gamification",
        "iap",
        "im_livechat",
        "link_tracker",
        "lunch",
        "mass_mailing",
        "note",
        "pad",
        "payment",
        "phone_validation",
        "portal",
        "pos_",
        "product",
        "rating",
        "resource",
        "sale_",
        "snailmail",
        "social",
        "spreadsheet",
        "survey",
        "test_",
        "theme_",
        "utm",
        "web_",
        "website_",
        "whatsapp",
        "discuss",
    }
)


class ModelRegistry:
    """Discovers and classifies Odoo models with access level resolution."""

    def __init__(
        self,
        model_access_config: dict[str, Any] | None = None,
        blocked_models: list[str] | None = None,
    ) -> None:
        """Initialise the registry.

        Parameters
        ----------
        model_access_config:
            Parsed YAML configuration with keys such as ``default_policy``,
            ``stock_models``, ``custom_models``.
        blocked_models:
            Models from ``restrictions.yaml`` ``always_blocked`` list.
        """
        self._models: dict[str, ModelInfo] = {}
        self._config = model_access_config or {}
        self._blocked: set[str] = set(blocked_models or [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def discover(self, client: Any) -> None:
        """Query ``ir.model`` for all installed models and populate the registry."""
        records = await client.execute_kw(
            "ir.model",
            "search_read",
            [[]],
            {
                "fields": [
                    "model",
                    "name",
                    "info",
                    "transient",
                    "state",
                    "modules",
                ],
                "limit": 0,
            },
        )
        self._models.clear()
        for rec in records:
            info = self._classify_model(rec)
            self._models[info.name] = info

    def get_model(self, name: str) -> ModelInfo | None:
        """Return model metadata or ``None`` if unknown."""
        return self._models.get(name)

    def get_accessible_models(self, is_admin: bool = False) -> list[ModelInfo]:
        """Return models the caller may access.

        Non-admin callers see ``FULL_CRUD`` and ``READ_ONLY`` models.
        Admin callers additionally see ``ADMIN_ONLY`` models.
        """
        allowed = {AccessLevel.FULL_CRUD, AccessLevel.READ_ONLY}
        if is_admin:
            allowed.add(AccessLevel.ADMIN_ONLY)
        return [m for m in self._models.values() if m.access_level in allowed]

    def search_models(self, query: str) -> list[ModelInfo]:
        """Case-insensitive search on model name and description."""
        if not query:
            return []
        q = query.lower()
        return [
            m
            for m in self._models.values()
            if q in m.name.lower() or q in m.description.lower()
        ]

    def is_custom_model(self, model_name: str) -> bool:
        """Return ``True`` if the model is classified as custom."""
        model = self._models.get(model_name)
        if model is None:
            return False
        return model.is_custom

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_model(self, model_data: dict[str, Any]) -> ModelInfo:
        """Build a ``ModelInfo`` from an ``ir.model`` record dict."""
        model_name: str = model_data.get("model", "")
        description: str = model_data.get("name", "")
        is_transient: bool = bool(model_data.get("transient", False))
        state: str = model_data.get("state", "base")
        modules_str: str = model_data.get("modules", "") or ""

        # The first module in the comma-separated list is considered the
        # originating module.
        modules_list = [m.strip() for m in modules_str.split(",") if m.strip()]
        module = modules_list[0] if modules_list else ""

        is_custom = self._is_custom_model_check(model_name, modules_list, state)
        access_level = self._determine_access_level(model_name, is_custom)

        return ModelInfo(
            name=model_name,
            description=description,
            is_custom=is_custom,
            is_transient=is_transient,
            module=module,
            state=state,
            access_level=access_level,
        )

    def _is_custom_model_check(
        self, model_name: str, modules: list[str], state: str
    ) -> bool:
        """Determine if a model is custom.

        A model is custom when:
        - Its state is ``"manual"`` (Studio models), **or**
        - **All** of its modules are non-stock.
        """
        if state == "manual":
            return True
        if not modules:
            return False
        return all(not self._is_stock_module(m) for m in modules)

    def _determine_access_level(self, model_name: str, is_custom: bool) -> AccessLevel:
        """Resolve access level from configuration lists and default policy."""
        # Always-blocked models override everything.
        if model_name in self._blocked:
            return AccessLevel.BLOCKED

        # Check explicit configuration lists.
        stock_cfg = self._config.get("stock_models", {}) or {}
        custom_cfg = self._config.get("custom_models", {}) or {}

        for level_key, level_enum in (
            ("full_crud", AccessLevel.FULL_CRUD),
            ("read_only", AccessLevel.READ_ONLY),
            ("admin_only", AccessLevel.ADMIN_ONLY),
        ):
            stock_list = stock_cfg.get(level_key) or []
            if model_name in stock_list:
                return level_enum

            custom_list = custom_cfg.get(level_key) or []
            if model_name in custom_list:
                return level_enum

        # Fall back to default policy.
        default = self._config.get("default_policy", "deny")
        if default == "allow":
            return AccessLevel.FULL_CRUD
        # Under "deny", unlisted models are accessible only to admins
        # (consistent with RestrictionChecker.check_model_access behavior)
        return AccessLevel.ADMIN_ONLY

    def _is_stock_module(self, module_name: str) -> bool:
        """Return ``True`` if *module_name* is a known Odoo stock module."""
        if module_name in STOCK_MODULE_PREFIXES:
            return True
        # Check prefixes that end with "_" (e.g. "l10n_", "sale_")
        for prefix in STOCK_MODULE_PREFIXES:
            if prefix.endswith("_") and module_name.startswith(prefix):
                return True
        return False
