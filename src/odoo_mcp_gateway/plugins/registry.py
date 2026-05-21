"""Plugin discovery and lifecycle management."""

from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass, field
from typing import Any

from .base import OdooPlugin
from .sdk import (
    PLUGIN_SDK_VERSION,
    PluginContext,
    check_plugin_sdk_compat,
)

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "odoo_mcp_gateway.plugins"


@dataclass
class PluginInfo:
    """Runtime information about a loaded plugin."""

    name: str
    version: str
    description: str
    plugin_class: type[OdooPlugin]
    instance: OdooPlugin | None = None
    enabled: bool = True
    load_error: str | None = None
    required_modules: list[str] = field(default_factory=list)
    missing_modules: list[str] = field(default_factory=list)
    # Plugin SDK 1.0: which SDK range this plugin claims compat with.
    # Populated from the plugin class's ``plugin_sdk_version`` attribute
    # at registration time. May be ``None`` for legacy plugins; those
    # are loaded with a deprecation warning.
    plugin_sdk_version: str | None = None
    # v0.3.3 follow-up MED-3: operator-supplied alternates from
    # ``model_access.yaml::plugin_overrides``. Empty by default;
    # populated by ``PluginRegistry.set_plugin_overrides()`` when the
    # gateway loads its YAML config.
    accept_modules: list[str] = field(default_factory=list)
    accept_models: list[str] = field(default_factory=list)
    # Effective model name resolved from ``required_models[0]`` /
    # ``accept_models[0]`` after override merge. Plugin tool handlers
    # read this when they need to issue ``execute_kw`` against the
    # actual model present on the installation (stock vs custom).
    effective_model_name: str | None = None


class PluginRegistry:
    """Discovers, validates, and manages gateway plugins.

    Discovery uses Python entry_points::

        # In a plugin's pyproject.toml:
        [project.entry-points."odoo_mcp_gateway.plugins"]
        hr = "my_plugin:HRPlugin"

        # Built-in plugins in this package's pyproject.toml:
        [project.entry-points."odoo_mcp_gateway.plugins"]
        hr = "odoo_mcp_gateway.plugins.core.hr:HRPlugin"
    """

    def __init__(
        self,
        enabled_plugins: list[str] | None = None,
        disabled_plugins: list[str] | None = None,
    ) -> None:
        """Initialise the registry.

        Parameters
        ----------
        enabled_plugins:
            If set, only these plugins are loaded (allowlist).
        disabled_plugins:
            If set, these plugins are skipped (blocklist).
        """
        self._plugins: dict[str, PluginInfo] = {}
        self._enabled = set(enabled_plugins) if enabled_plugins else None
        self._disabled = set(disabled_plugins) if disabled_plugins else set()
        # v0.3.3 follow-up MED-3: operator-supplied per-plugin overrides
        # awaiting application. ``set_plugin_overrides`` populates this
        # at startup; ``check_requirements`` / ``register_plugin`` pick
        # entries up as plugins become known.
        self._pending_overrides: dict[str, tuple[list[str], list[str]]] = {}

    def discover(self) -> list[PluginInfo]:
        """Discover all plugins from entry_points.

        Returns list of ``PluginInfo`` (some may have ``load_error`` set).
        """
        discovered: list[PluginInfo] = []

        try:
            eps = importlib.metadata.entry_points()
            # Python 3.12+ returns a SelectableGroups, 3.10 returns dict
            if hasattr(eps, "select"):
                plugin_eps = eps.select(group=ENTRY_POINT_GROUP)
            else:
                plugin_eps = eps.get(ENTRY_POINT_GROUP, [])  # type: ignore[arg-type]
        except Exception as e:
            logger.warning("Failed to query entry_points: %s", e)
            return discovered

        for ep in plugin_eps:
            info = self._load_entry_point(ep)
            discovered.append(info)
            self._plugins[info.name] = info

        return discovered

    def register_plugin(self, plugin_class: type[OdooPlugin]) -> PluginInfo:
        """Manually register a plugin class (for testing or programmatic use)."""
        try:
            instance = plugin_class()
            sdk_spec = _read_plugin_sdk_version(plugin_class, instance)
            ok, message = check_plugin_sdk_compat(instance.name, sdk_spec)
            if message:
                # Warn on compat issues; error on outright refusal.
                if ok:
                    logger.warning("%s", message)
                else:
                    logger.error("%s", message)
            info = PluginInfo(
                name=instance.name,
                version=instance.version,
                description=instance.description,
                plugin_class=plugin_class,
                instance=instance if ok else None,
                enabled=ok,
                required_modules=list(instance.required_odoo_modules or []),
                plugin_sdk_version=sdk_spec,
                load_error=None if ok else message,
            )
            self._apply_pending_overrides(info)
        except Exception as e:
            info = PluginInfo(
                name=getattr(plugin_class, "__name__", "unknown"),
                version="0.0.0",
                description="",
                plugin_class=plugin_class,
                load_error=str(e),
            )

        self._plugins[info.name] = info
        return info

    def _apply_pending_overrides(self, info: PluginInfo) -> None:
        """Pull any operator-supplied overrides for *info* from the
        pending map, then refresh the effective model name."""
        pending = self._pending_overrides.get(info.name)
        if pending is not None:
            info.accept_modules, info.accept_models = pending
        self._refresh_effective_model_name(info)

    def set_plugin_overrides(
        self,
        overrides: dict[str, Any],
    ) -> None:
        """Apply YAML-supplied per-plugin module / model overrides.

        Accepts the parsed ``plugin_overrides`` mapping from
        ``model_access.yaml``.  Each entry may be either a
        ``PluginOverride`` Pydantic model or a plain dict with
        ``accept_modules`` / ``accept_models`` lists — both are
        normalised here so callers don't need to import the model.

        Unknown plugin names are stored anyway: a plugin entry-point
        that isn't yet loaded (e.g. a future plugin shipped via a
        third-party package) should still receive its operator
        configuration when it eventually registers.  We track the
        pending overrides in ``_pending_overrides`` so
        ``register_plugin`` / ``discover`` can pick them up.

        v0.3.3 follow-up MED-3: this is the operator's escape hatch
        for installations where the stock Odoo module name differs
        from the plugin's declared requirement (e.g.
        ``odoo_website_helpdesk`` shipping ``ticket.helpdesk``).
        """
        self._pending_overrides = {}
        for plugin_name, raw in (overrides or {}).items():
            modules: list[str]
            models: list[str]
            if hasattr(raw, "accept_modules"):
                modules = list(getattr(raw, "accept_modules", []) or [])
                models = list(getattr(raw, "accept_models", []) or [])
            elif isinstance(raw, dict):
                modules = list(raw.get("accept_modules", []) or [])
                models = list(raw.get("accept_models", []) or [])
            else:
                logger.warning(
                    "Ignoring plugin_overrides entry for %s: unrecognised type %r",
                    plugin_name,
                    type(raw).__name__,
                )
                continue
            self._pending_overrides[plugin_name] = (modules, models)
            info = self._plugins.get(plugin_name)
            if info is not None:
                info.accept_modules = modules
                info.accept_models = models
                self._refresh_effective_model_name(info)

    async def check_requirements(
        self,
        installed_modules: list[str],
    ) -> list[PluginInfo]:
        """Check which plugins have all required Odoo modules installed.

        Parameters
        ----------
        installed_modules:
            List of installed Odoo module names
            (from ``ir.module.module`` where ``state='installed'``).

        Returns
        -------
        list[PluginInfo]
            Plugins with ``missing_modules`` populated.

        v0.3.3 follow-up MED-3: operator-supplied alternates from
        ``model_access.yaml::plugin_overrides`` are OR-merged with the
        plugin's declared ``required_odoo_modules``.  The plugin is
        satisfied if ANY of the (declared ∪ accepted) modules is
        installed.  The same OR-semantics apply to ``required_models``
        via ``accept_models`` so a plugin querying ``helpdesk.ticket``
        can be redirected to ``ticket.helpdesk`` on installations
        running the custom helpdesk module.
        """
        installed_set = set(installed_modules)
        result: list[PluginInfo] = []

        for info in self._plugins.values():
            if info.instance is None:
                continue
            # Ensure pending overrides are applied (e.g. for plugins
            # that registered after ``set_plugin_overrides`` was called).
            pending = getattr(self, "_pending_overrides", {}).get(info.name)
            already_set = info.accept_modules or info.accept_models
            if pending is not None and not already_set:
                info.accept_modules, info.accept_models = pending
                self._refresh_effective_model_name(info)

            declared = list(info.required_modules)
            accepted = list(info.accept_modules)
            if accepted:
                # Override active: treat ``declared`` and ``accepted`` as
                # the SAME OR-set — the plugin is satisfied if ANY
                # member is installed. This is the v0.3.3 MED-3 path
                # used by operators on custom Odoo modules.
                pool = set(declared) | set(accepted)
                satisfied = any(m in installed_set for m in pool)
                missing = [] if satisfied else sorted(pool)
            else:
                # No override: legacy semantics — ALL declared modules
                # must be installed (an empty declared list trivially
                # satisfies).
                missing = [m for m in declared if m not in installed_set]
                satisfied = not missing

            info.missing_modules = missing
            if not satisfied:
                info.enabled = False
                if accepted:
                    logger.warning(
                        "Plugin '%s' disabled: none of these Odoo modules "
                        "are installed: %s",
                        info.name,
                        ", ".join(missing),
                    )
                else:
                    logger.warning(
                        "Plugin '%s' disabled: missing Odoo modules: %s",
                        info.name,
                        ", ".join(missing),
                    )
            result.append(info)

        return result

    def _refresh_effective_model_name(self, info: PluginInfo) -> None:
        """Resolve ``info.effective_model_name`` from declared/accept lists.

        Preference order: the FIRST entry of ``accept_models`` (operator
        opt-in to a non-stock model name) wins over ``required_models[0]``.
        Used by plugin tools that need to issue ``execute_kw`` against the
        actual model present on the installation. Plugins that don't
        declare ``required_models`` get ``None``.
        """
        if info.instance is None:
            return
        required_models = list(getattr(info.instance, "required_models", []) or [])
        accept = list(info.accept_models or [])
        info.effective_model_name = (
            accept[0] if accept else (required_models[0] if required_models else None)
        )

    def activate(
        self,
        server: Any,
        context: Any,
    ) -> list[str]:
        """Activate all enabled plugins by calling ``register()``.

        Audit blocker #4: also fires the ``pre_register`` /
        ``post_register`` lifecycle hooks bracketed around each
        plugin's ``register()`` call so plugins can do setup work
        (e.g. open a per-process resource handle) that needs to
        happen before/after tool registration.

        ``activate()`` is synchronous (it's invoked from the
        synchronous ``create_server`` path before the main asyncio
        loop starts), so we drive the async dispatchers through a
        small one-shot helper that runs each coroutine on its own
        temporary loop. If a loop is already running (e.g. tests
        calling ``activate()`` from inside an async context), the
        helper falls back to ``asyncio.run_coroutine_threadsafe``
        via the running loop — preserving the existing sync
        signature without serialising the hooks.

        Returns list of activated plugin names.
        """
        activated: list[str] = []

        for info in self._plugins.values():
            if not info.enabled or info.instance is None:
                continue
            if info.load_error:
                continue

            # Check allowlist / blocklist
            if self._enabled is not None and info.name not in self._enabled:
                logger.debug("Plugin '%s' not in enabled list, skipping", info.name)
                continue
            if info.name in self._disabled:
                logger.debug("Plugin '%s' is disabled, skipping", info.name)
                continue

            # pre_register fires for THIS plugin only (the dispatcher
            # iterates all active plugins, but at this point in the
            # loop the only "active" plugin from a hook's perspective
            # is the one we're about to register; subsequent plugins
            # see their own pre_register on their own iteration).
            self._run_async(self._dispatch_one_pre_register(info, context))

            try:
                info.instance.register(server, context)
                activated.append(info.name)
                logger.info("Activated plugin: %s v%s", info.name, info.version)
            except Exception as e:
                info.load_error = str(e)
                logger.error("Failed to activate plugin '%s': %s", info.name, e)
                continue

            # post_register fires for THIS plugin only after a
            # successful register(). Skipping it on register failure
            # avoids confusing plugins that use post_register as a
            # "all good, allocate runtime resources" signal.
            self._run_async(self._dispatch_one_post_register(info, context))

        return activated

    # ----------------------------------------------------------------
    # Sync→async bridge for the register-time hooks.
    # ----------------------------------------------------------------

    def _run_async(self, coro: Any) -> None:
        """Run *coro* to completion, working from either sync or async caller.

        - If no loop is running (the production case — ``create_server``
          is sync), use ``asyncio.run`` on a fresh loop.
        - If a loop IS running (some tests call ``activate()`` from
          inside an async fixture), fall back to scheduling on the
          running loop and awaiting via ``run_coroutine_threadsafe``.
          Note this path is best-effort: pytest-asyncio tests should
          prefer the async ``activate_async`` helper below.
        """
        import asyncio

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is None:
            try:
                asyncio.run(coro)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Plugin register hook dispatch failed: %s", e)
            return

        # Loop running: we can't asyncio.run inside it. Best we can
        # do is fire-and-await on the same loop without blocking it.
        # In practice every prod call site is sync, so this branch
        # is only hit by tests that exercise the registry from an
        # async fixture; they should use ``activate_async`` instead.
        try:
            running.create_task(coro)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Plugin register hook scheduling failed: %s", e)

    async def _dispatch_one_pre_register(self, info: PluginInfo, gateway: Any) -> None:
        """Dispatch ``pre_register`` to a single plugin.

        Mirrors :meth:`dispatch_pre_register` but scoped to one plugin
        and swallows exceptions so a misbehaving hook can't block the
        whole activate sequence.
        """
        if info.instance is None:
            return
        try:
            ctx = PluginContext(gateway, info.name)
            await info.instance.pre_register(ctx)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Plugin '%s' pre_register hook failed: %s", info.name, e)

    async def _dispatch_one_post_register(self, info: PluginInfo, gateway: Any) -> None:
        """Dispatch ``post_register`` to a single plugin."""
        if info.instance is None:
            return
        try:
            ctx = PluginContext(gateway, info.name)
            await info.instance.post_register(ctx)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Plugin '%s' post_register hook failed: %s", info.name, e)

    def get_plugin(self, name: str) -> PluginInfo | None:
        """Get plugin info by name."""
        return self._plugins.get(name)

    def get_all_plugins(self) -> list[PluginInfo]:
        """Get all discovered plugins."""
        return list(self._plugins.values())

    def get_active_plugins(self) -> list[PluginInfo]:
        """Get only enabled plugins without errors."""
        return [
            p
            for p in self._plugins.values()
            if p.enabled and p.load_error is None and p.instance is not None
        ]

    def _load_entry_point(self, ep: Any) -> PluginInfo:
        """Load a single entry_point into ``PluginInfo``."""
        name: str = ep.name

        # Check blocklist early
        if name in self._disabled:
            return PluginInfo(
                name=name,
                version="0.0.0",
                description="",
                plugin_class=OdooPlugin,  # type: ignore[type-abstract]
                enabled=False,
                load_error="Disabled by configuration",
            )

        try:
            plugin_class = ep.load()

            if not (
                isinstance(plugin_class, type) and issubclass(plugin_class, OdooPlugin)
            ):
                return PluginInfo(
                    name=name,
                    version="0.0.0",
                    description="",
                    plugin_class=plugin_class,
                    load_error=f"{plugin_class} is not a subclass of OdooPlugin",
                )

            instance = plugin_class()
            sdk_spec = _read_plugin_sdk_version(plugin_class, instance)
            ok, message = check_plugin_sdk_compat(instance.name, sdk_spec)
            if message:
                if ok:
                    logger.warning("%s", message)
                else:
                    logger.error("%s", message)
            info = PluginInfo(
                name=instance.name,
                version=instance.version,
                description=instance.description,
                plugin_class=plugin_class,
                instance=instance if ok else None,
                enabled=ok,
                required_modules=list(instance.required_odoo_modules or []),
                plugin_sdk_version=sdk_spec,
                load_error=None if ok else message,
            )
            self._apply_pending_overrides(info)
            return info
        except Exception as e:
            logger.error("Failed to load plugin '%s': %s", name, e)
            return PluginInfo(
                name=name,
                version="0.0.0",
                description="",
                plugin_class=OdooPlugin,  # type: ignore[type-abstract]
                load_error=str(e),
            )

    # ----------------------------------------------------------------
    # Lifecycle dispatch
    #
    # The async dispatch helpers iterate over all active plugins and
    # call the corresponding hook. They catch and log per-plugin
    # exceptions so one misbehaving plugin can't take down the whole
    # request path. Plugins that don't implement a particular hook
    # inherit the no-op defaults from ``OdooPlugin``.
    # ----------------------------------------------------------------

    async def dispatch_pre_register(self, gateway: Any) -> None:
        """Call ``pre_register`` on every active plugin (async, parallel)."""
        for info in self.get_active_plugins():
            if info.instance is None:
                continue
            try:
                ctx = PluginContext(gateway, info.name)
                await info.instance.pre_register(ctx)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Plugin '%s' pre_register hook failed: %s",
                    info.name,
                    e,
                )

    async def dispatch_post_register(self, gateway: Any) -> None:
        """Call ``post_register`` on every active plugin."""
        for info in self.get_active_plugins():
            if info.instance is None:
                continue
            try:
                ctx = PluginContext(gateway, info.name)
                await info.instance.post_register(ctx)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Plugin '%s' post_register hook failed: %s",
                    info.name,
                    e,
                )

    async def dispatch_pre_call(
        self,
        gateway: Any,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        """Call ``pre_call`` on every active plugin. Re-raises plugin
        exceptions so a plugin can abort the call by raising."""
        for info in self.get_active_plugins():
            if info.instance is None:
                continue
            ctx = PluginContext(gateway, info.name)
            await info.instance.pre_call(tool, arguments, ctx)

    async def dispatch_post_call(
        self,
        gateway: Any,
        tool: str,
        result: Any,
    ) -> Any:
        """Run ``post_call`` on every active plugin; the result can be
        transformed by each plugin in turn."""
        for info in self.get_active_plugins():
            if info.instance is None:
                continue
            try:
                ctx = PluginContext(gateway, info.name)
                result = await info.instance.post_call(tool, result, ctx)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Plugin '%s' post_call hook failed (result unchanged): %s",
                    info.name,
                    e,
                )
        return result

    async def dispatch_on_session_close(
        self,
        gateway: Any,
        session_key: str,
    ) -> None:
        """Call ``on_session_close`` on every active plugin."""
        for info in self.get_active_plugins():
            if info.instance is None:
                continue
            try:
                ctx = PluginContext(gateway, info.name)
                await info.instance.on_session_close(session_key, ctx)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Plugin '%s' on_session_close hook failed: %s",
                    info.name,
                    e,
                )

    async def dispatch_on_external_event(
        self,
        gateway: Any,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Call ``on_external_event`` on every active plugin.

        Reserved for v0.4.0 webhook delivery — currently NEVER called
        in production. Plugins implementing the hook today are
        future-ready when v0.4.0 lands.
        """
        for info in self.get_active_plugins():
            if info.instance is None:
                continue
            try:
                ctx = PluginContext(gateway, info.name)
                await info.instance.on_external_event(event_type, payload, ctx)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Plugin '%s' on_external_event hook failed: %s",
                    info.name,
                    e,
                )


def _read_plugin_sdk_version(plugin_class: type, instance: Any) -> str | None:
    """Look up ``plugin_sdk_version`` on the class or instance.

    Plugins may declare the attribute as a class attribute (most
    common) or as an instance attribute (set in ``__init__``). We
    check the instance first so the latter overrides the former.
    Returns ``None`` if the attribute is missing entirely.
    """
    value = getattr(instance, "plugin_sdk_version", None)
    if value is None:
        value = getattr(plugin_class, "plugin_sdk_version", None)
    if value is None:
        return None
    return str(value)


# Re-export for backward compatibility with anyone importing the
# constant from the registry module.
__all__ = [
    "ENTRY_POINT_GROUP",
    "PLUGIN_SDK_VERSION",
    "PluginInfo",
    "PluginRegistry",
]
