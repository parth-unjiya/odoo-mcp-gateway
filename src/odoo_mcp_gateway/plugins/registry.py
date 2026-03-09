"""Plugin discovery and lifecycle management."""

from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass, field
from typing import Any

from .base import OdooPlugin

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
            info = PluginInfo(
                name=instance.name,
                version=instance.version,
                description=instance.description,
                plugin_class=plugin_class,
                instance=instance,
                required_modules=instance.required_odoo_modules,
            )
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
        """
        installed_set = set(installed_modules)
        result: list[PluginInfo] = []

        for info in self._plugins.values():
            if info.instance is None:
                continue
            missing = [m for m in info.required_modules if m not in installed_set]
            info.missing_modules = missing
            if missing:
                info.enabled = False
                logger.warning(
                    "Plugin '%s' disabled: missing Odoo modules: %s",
                    info.name,
                    ", ".join(missing),
                )
            result.append(info)

        return result

    def activate(
        self,
        server: Any,
        context: Any,
    ) -> list[str]:
        """Activate all enabled plugins by calling ``register()``.

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

            try:
                info.instance.register(server, context)
                activated.append(info.name)
                logger.info("Activated plugin: %s v%s", info.name, info.version)
            except Exception as e:
                info.load_error = str(e)
                logger.error("Failed to activate plugin '%s': %s", info.name, e)

        return activated

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
            return PluginInfo(
                name=instance.name,
                version=instance.version,
                description=instance.description,
                plugin_class=plugin_class,
                instance=instance,
                required_modules=instance.required_odoo_modules,
            )
        except Exception as e:
            logger.error("Failed to load plugin '%s': %s", name, e)
            return PluginInfo(
                name=name,
                version="0.0.0",
                description="",
                plugin_class=OdooPlugin,  # type: ignore[type-abstract]
                load_error=str(e),
            )
