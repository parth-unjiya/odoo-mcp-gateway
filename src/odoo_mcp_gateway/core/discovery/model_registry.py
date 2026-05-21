"""Model registry: discovers and classifies Odoo models."""

from __future__ import annotations

import time
from typing import Any

from .models import AccessLevel, ModelInfo

# Cache key for per-session model maps.  ``session_key`` mirrors the
# ContextVar-bound key used by :class:`FieldInspector`; ``None`` denotes
# stdio / single-user mode and keeps the historical single-entry
# behaviour.  v0.3.3 follow-up: ``ir.model.search_read`` is scoped by
# Odoo's per-user ``ir.model.access`` rules, so a model list discovered
# by an admin must not be served to a portal user on a multi-tenant
# HTTP deployment.
_SessionKey = str | None

# Sentinel used in default-parameter slots that need a distinct "the
# caller did not pass a value, use the current ContextVar" signal,
# because ``None`` is a legitimate session-key value (stdio mode).
# Use ``is`` comparison, not ``==``.
_USE_CURRENT_SESSION: Any = object()

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
        cache_ttl: int = 3600,
    ) -> None:
        """Initialise the registry.

        Parameters
        ----------
        model_access_config:
            Parsed YAML configuration with keys such as ``default_policy``,
            ``stock_models``, ``custom_models``.
        blocked_models:
            Models from ``restrictions.yaml`` ``always_blocked`` list.
        cache_ttl:
            Per-session discovery TTL in seconds.  After this many
            seconds without re-discovery the session's entry is
            considered stale.  Stdio mode (no session key bound) uses
            the same TTL but keeps a single shared entry.
        """
        # v0.3.3 follow-up MED: per-session map of discovered models.
        # ``None`` is the canonical stdio / single-user key.
        self._sessions: dict[_SessionKey, tuple[float, dict[str, ModelInfo]]] = {}
        self._config = model_access_config or {}
        self._blocked: set[str] = set(blocked_models or [])
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def discover(self, client: Any) -> None:
        """Query ``ir.model`` for all installed models and populate the
        per-session registry entry.

        The ``ir.model.search_read`` call is scoped by the active
        session's Odoo ACLs (``ir.model.access``), so the result must
        be cached under the calling session's key. Stdio mode keeps
        the historical single-entry behaviour because no session key
        is bound.
        """
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
        models: dict[str, ModelInfo] = {}
        for rec in records:
            info = self._classify_model(rec)
            models[info.name] = info
        self._sessions[self._session_key()] = (time.monotonic(), models)

    def get_model(self, name: str) -> ModelInfo | None:
        """Return model metadata or ``None`` if unknown."""
        return self._current_models().get(name)

    def get_accessible_models(self, is_admin: bool = False) -> list[ModelInfo]:
        """Return models the caller may access.

        Non-admin callers see ``FULL_CRUD`` and ``READ_ONLY`` models.
        Admin callers additionally see ``ADMIN_ONLY`` models.
        """
        allowed = {AccessLevel.FULL_CRUD, AccessLevel.READ_ONLY}
        if is_admin:
            allowed.add(AccessLevel.ADMIN_ONLY)
        return [m for m in self._current_models().values() if m.access_level in allowed]

    def search_models(self, query: str) -> list[ModelInfo]:
        """Case-insensitive search on model name and description."""
        if not query:
            return []
        q = query.lower()
        return [
            m
            for m in self._current_models().values()
            if q in m.name.lower() or q in m.description.lower()
        ]

    def is_custom_model(self, model_name: str) -> bool:
        """Return ``True`` if the model is classified as custom."""
        model = self._current_models().get(model_name)
        if model is None:
            return False
        return model.is_custom

    def has_discovered(self, session_key: Any = _USE_CURRENT_SESSION) -> bool:
        """Return ``True`` if the calling session has a fresh discovery.

        ``session_key`` defaults to the :data:`_USE_CURRENT_SESSION`
        sentinel meaning "use the current ContextVar-bound session".
        Pass an explicit key (or ``None`` for stdio) to query a
        specific session.  Callers in :mod:`tools.schema` use this to
        decide whether ``discover()`` needs to fire for the current
        session — replacing the legacy global ``_models_discovered``
        flag.
        """
        if session_key is _USE_CURRENT_SESSION:
            key: _SessionKey = self._session_key()
        else:
            key = session_key
        entry = self._sessions.get(key)
        if entry is None:
            return False
        ts, _ = entry
        return (time.monotonic() - ts) < self._cache_ttl

    def invalidate(self, session_key: Any = _USE_CURRENT_SESSION) -> None:
        """Drop the cached discovery for a single session.

        Default :data:`_USE_CURRENT_SESSION` evicts the current
        ContextVar-bound session; pass ``None`` (stdio) or an explicit
        session key to target a different entry.  Pass ``"*"`` to
        clear every session (full reset, used by tests and reload
        paths).
        """
        if session_key == "*":
            self._sessions.clear()
            return
        if session_key is _USE_CURRENT_SESSION:
            key: _SessionKey = self._session_key()
        else:
            key = session_key
        self._sessions.pop(key, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_key(self) -> _SessionKey:
        """Resolve the current MCP session key, defaulting to ``None``.

        Imported locally to avoid the circular dependency between the
        discovery layer and :mod:`odoo_mcp_gateway.server` (the same
        pattern used by :class:`FieldInspector`).
        """
        try:
            from odoo_mcp_gateway.server import get_current_session_key

            return get_current_session_key()
        except Exception:  # pragma: no cover - defensive
            return None

    def _current_models(self) -> dict[str, ModelInfo]:
        """Return the model dict for the current session (or empty)."""
        entry = self._sessions.get(self._session_key())
        if entry is None:
            return {}
        return entry[1]

    # ------------------------------------------------------------------
    # Backwards-compatibility shim
    #
    # Pre-v0.3.3 callers (and a handful of tests) read/write
    # ``self._models`` directly. We expose it as a property over the
    # current session's entry so existing call sites keep working
    # while the storage is migrated to the per-session map. Stdio
    # mode (no session key bound) reads/writes the ``None`` entry,
    # which is exactly the single-entry behaviour callers expected.
    # ------------------------------------------------------------------

    @property
    def _models(self) -> dict[str, ModelInfo]:
        """Current-session model dict (BC shim — prefer ``get_model`` /
        ``get_accessible_models``)."""
        key = self._session_key()
        entry = self._sessions.get(key)
        if entry is None:
            # Materialise an empty entry so mutations on the returned
            # dict survive (legacy code does ``registry._models[...]
            # = ModelInfo(...)`` directly).
            self._sessions[key] = (time.monotonic(), {})
            entry = self._sessions[key]
        return entry[1]

    @_models.setter
    def _models(self, value: dict[str, ModelInfo]) -> None:
        self._sessions[self._session_key()] = (time.monotonic(), value)

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
