"""Plugin SDK 1.0 — the public contract for odoo-mcp-gateway plugins.

This module is the **only** thing external plugin authors need to
import. It provides:

* :class:`OdooMcpPlugin` — a ``@runtime_checkable`` typing.Protocol
  declaring the surface every plugin must implement.
* :data:`PLUGIN_SDK_VERSION` — the current SDK version. Plugins
  declare their compatibility range as ``plugin_sdk_version =
  ">=1.0,<2.0"``; the registry checks this against the running
  gateway and either loads, warns, or refuses.
* :class:`PluginContext` — the runtime context passed to every
  lifecycle hook. A thin facade over ``GatewayContext`` that exposes
  only the surface plugin authors should use (no internal helpers).
* :func:`check_plugin_sdk_compat` — the compat check used by the
  registry. Two-tier policy: same major → warn + load; different
  major → refuse.

Design (see ``.release-drafts/v030-plan.md`` ADR-003):

* ``Protocol`` instead of an ABC so external authors don't import
  the gateway's implementation at runtime — they only need the
  Protocol for type-checking.
* SemVer for the SDK with a strict compat range. ``1.x`` SDK
  guarantees the Protocol surface; ``2.0`` reserved for the next
  breaking change.
* 7 lifecycle hooks — pre_register, register, post_register,
  pre_call, post_call, on_session_close, on_external_event. All
  except ``register`` are async. ``on_external_event`` is reserved
  for v0.4.0 webhooks but shipped now so plugin authors can prepare.

The existing :class:`OdooPlugin` ABC in ``plugins/base.py`` stays
as a convenience base class that implements this Protocol with no-op
defaults. Existing plugins keep working unchanged — the migration to
declare ``plugin_sdk_version`` is additive.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# SDK Version
# ---------------------------------------------------------------------
#
# Bumps:
# * Major (2.0, 3.0, ...) — breaking change to the Protocol surface.
#   Adding a required new hook or changing an existing hook's
#   signature warrants a major bump. Plugins targeting an older
#   major are REFUSED by the registry.
# * Minor (1.1, 1.2, ...) — additive change. New optional hook,
#   new optional context attribute. Plugins targeting an older
#   minor are LOADED with a warning.
# * Patch (1.0.1, ...) — clarifications / doc fixes only. No
#   compat impact.
#
# Update CHANGELOG when bumping.
PLUGIN_SDK_VERSION: str = "1.0.0"


# ---------------------------------------------------------------------
# PluginContext — what plugins are allowed to see
# ---------------------------------------------------------------------


class PluginContext:
    """Per-call context handed to plugin lifecycle hooks.

    Wraps :class:`GatewayContext` and exposes only the surface plugin
    authors should depend on. Keeps internal helpers (`_auth_lock`,
    `_models_discovered`, raw `auth_managers` dict) out of the plugin
    contract so we can refactor them without breaking plugins.

    Plugins can access:
    * ``gateway`` — the full GatewayContext, for backward compat with
      pre-SDK plugins. NEW plugins should prefer the focused helpers
      below.
    * ``rbac``, ``restrictions``, ``audit_logger``, ``error_sanitizer``
      — the security primitives plugin tools need.
    * ``field_inspector``, ``model_registry`` — the discovery
      primitives. Plugins reading fields/models should use these
      rather than direct ``execute_kw`` so caching works.
    * ``version_adapter`` — the active Odoo version adapter (may be
      ``None`` if version detection failed). Plugins doing
      version-aware work read ``adapter.major_version``.
    * ``plugin_sdk_version`` — the SDK version the GATEWAY is running.
      Plugins can branch on this if they support multiple SDK majors.
    """

    def __init__(self, gateway: GatewayContext, plugin_name: str) -> None:
        self.gateway = gateway
        self.plugin_name = plugin_name
        self.plugin_sdk_version = PLUGIN_SDK_VERSION

    # Convenience accessors — keep plugin code from poking around in
    # gateway internals.
    @property
    def rbac(self) -> Any:
        return self.gateway.rbac

    @property
    def restrictions(self) -> Any:
        return self.gateway.restrictions

    @property
    def audit_logger(self) -> Any:
        return self.gateway.audit_logger

    @property
    def error_sanitizer(self) -> Any:
        return self.gateway.error_sanitizer

    @property
    def field_inspector(self) -> Any:
        return self.gateway.field_inspector

    @property
    def model_registry(self) -> Any:
        return self.gateway.model_registry

    @property
    def version_adapter(self) -> Any:
        return self.gateway.version_adapter


# ---------------------------------------------------------------------
# OdooMcpPlugin — the Protocol every plugin implements
# ---------------------------------------------------------------------


@runtime_checkable
class OdooMcpPlugin(Protocol):
    """Protocol for odoo-mcp-gateway plugins.

    External plugin authors implement this Protocol; they do NOT
    need to subclass the gateway's ``OdooPlugin`` ABC unless they
    want the no-op default hook implementations it provides.

    Required attributes:

    * ``name`` — unique plugin identifier (e.g. ``"hr"``,
      ``"sales"``, ``"my_org_compliance"``).
    * ``version`` — plugin's own SemVer string.
    * ``plugin_sdk_version`` — SDK compat range, e.g.
      ``">=1.0,<2.0"``. See :func:`check_plugin_sdk_compat`.
    * ``required_odoo_modules`` — Odoo modules the plugin needs.
      The registry checks these against ``ir.module.module`` at
      login time and disables plugins whose modules aren't
      installed.
    * ``required_models`` — Odoo models the plugin queries.

    Required method:

    * :meth:`register` — synchronous, called once at server startup
      to register MCP tools / resources / prompts.

    Optional lifecycle hooks (all async). Default implementations
    do nothing — plugins override only what they need:

    * :meth:`pre_register` — runs AFTER an Odoo login completes
      (so plugin can query Odoo) but BEFORE :meth:`register`.
      Use for adaptive registration (e.g. only register a tool
      if a specific Odoo addon is present).
    * :meth:`post_register` — runs after every plugin has called
      :meth:`register`. Use for cross-plugin discovery
      (e.g. "wrap every helpdesk_user tool with extra audit logging").
    * :meth:`pre_call` — runs before every tool invocation. Returns
      ``None`` to allow the call, or raises to abort. Use for
      cross-cutting policies (e.g. quota enforcement, anomaly
      detection).
    * :meth:`post_call` — runs after every tool returns. May
      transform the result (return value replaces it). Use for
      output sanitization, response augmentation.
    * :meth:`on_session_close` — runs when a user's session is
      evicted or times out. Use for cleanup (close per-user
      caches, flush per-user audit buffers).
    * :meth:`on_external_event` — RESERVED for v0.4.0 webhook
      delivery. Implement now to be future-ready; today it's
      never called.
    """

    name: str
    version: str
    plugin_sdk_version: str
    required_odoo_modules: tuple[str, ...] | list[str]
    required_models: tuple[str, ...] | list[str]

    def register(self, server: FastMCP, context: PluginContext) -> None:
        """Register tools / resources / prompts. Synchronous, called once."""

    async def pre_register(self, context: PluginContext) -> None:
        """Optional async hook before register(). Default: no-op."""

    async def post_register(self, context: PluginContext) -> None:
        """Optional async hook after all plugins are registered. Default: no-op."""

    async def pre_call(
        self,
        tool: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> None:
        """Optional async hook before every tool call. Default: no-op.

        Raising aborts the call (the exception propagates to the
        caller, sanitised by the error_sanitizer).
        """

    async def post_call(
        self,
        tool: str,
        result: Any,
        context: PluginContext,
    ) -> Any:
        """Optional async hook after every tool call. Default: return result unchanged.

        Returning a value REPLACES the tool's result; returning the
        input result is the explicit no-op.
        """

    async def on_session_close(
        self,
        session_key: str,
        context: PluginContext,
    ) -> None:
        """Optional async hook when a session is evicted. Default: no-op."""

    async def on_external_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        context: PluginContext,
    ) -> None:
        """RESERVED for v0.4.0 webhook delivery. Default: no-op."""


# ---------------------------------------------------------------------
# Compat checking
# ---------------------------------------------------------------------


def check_plugin_sdk_compat(
    plugin_name: str,
    plugin_sdk_specifier: str | None,
) -> tuple[bool, str | None]:
    """Determine whether a plugin's SDK version range is compatible.

    Returns ``(load_ok, warning_message)``:

    * ``(True, None)`` — version range matches the running SDK; load it.
    * ``(True, "<warning>")`` — minor mismatch (same major); load + warn.
    * ``(False, "<error>")`` — major mismatch or unparseable spec;
      refuse to load.

    Two tiers of failure mode:

    1. **Same major, different minor** (e.g. plugin wants ``>=1.5,<2.0``
       but core is ``1.0``). Plugin code may reference SDK features
       that don't exist yet, but the Protocol surface is stable
       within a major version — so load with a warning and let the
       plugin author know to relax their version range.

    2. **Different major** (e.g. plugin wants ``>=2.0,<3.0`` but
       core is ``1.0``). Plugin code may reference Protocol fields
       that haven't been added or have been removed in the core's
       SDK version. Refuse to load — running it would likely crash
       at first hook dispatch.

    Plugins that omit ``plugin_sdk_version`` entirely (legacy or
    quick-and-dirty plugins) are loaded with a deprecation warning
    so authors notice the missing declaration.
    """
    if plugin_sdk_specifier is None or plugin_sdk_specifier == "":
        return True, (
            f"Plugin '{plugin_name}' did not declare plugin_sdk_version. "
            f'Add `plugin_sdk_version = ">={PLUGIN_SDK_VERSION},<2.0"` '
            "to the plugin class so the registry can verify SDK compat."
        )

    try:
        specifier = SpecifierSet(plugin_sdk_specifier)
    except InvalidSpecifier as exc:
        return False, (
            f"Plugin '{plugin_name}' has an invalid plugin_sdk_version "
            f"specifier {plugin_sdk_specifier!r}: {exc}"
        )

    try:
        core_version = Version(PLUGIN_SDK_VERSION)
    except InvalidVersion:  # pragma: no cover - SDK version is hard-coded
        return False, (
            f"Core PLUGIN_SDK_VERSION {PLUGIN_SDK_VERSION!r} is unparseable; "
            "this is a bug in the gateway, not the plugin."
        )

    if core_version in specifier:
        return True, None

    # The plugin's range doesn't include the core SDK. Determine
    # whether it's a major mismatch (refuse) or just a minor
    # mismatch (warn + load).
    core_major = core_version.major
    requested_majors = _extract_required_majors(specifier, plugin_sdk_specifier)
    if requested_majors and core_major not in requested_majors:
        return False, (
            f"Plugin '{plugin_name}' targets SDK {plugin_sdk_specifier} "
            f"but core is {PLUGIN_SDK_VERSION} (major mismatch). Refusing "
            "to load — plugin may reference Protocol fields that don't "
            "exist on this gateway."
        )

    # Same-major mismatch — load with warning.
    return True, (
        f"Plugin '{plugin_name}' targets SDK {plugin_sdk_specifier} "
        f"but core is running {PLUGIN_SDK_VERSION}. Loading anyway "
        "(same major version); consider updating the plugin's "
        "plugin_sdk_version range."
    )


def _extract_required_majors(specifier: SpecifierSet, raw: str) -> set[int]:
    """Inspect the SpecifierSet to figure out which major versions
    the plugin's range actually targets.

    Heuristic — we look at the upper bound (``<X``, ``<=X``,
    ``==X.*``) and the lower bound (``>=X``, ``>X``) to derive the
    set of accepted major versions. If we can't parse a useful
    bound, return empty (treat as "major-agnostic" → warn-and-load).
    """
    majors: set[int] = set()
    # We piggyback on packaging.Version by checking common boundary
    # values in the range [0..99]. That's a wide net but
    # avoids reimplementing the specifier algebra.
    for candidate_major in range(0, 100):
        candidate = Version(f"{candidate_major}.0.0")
        if candidate in specifier:
            majors.add(candidate_major)
        else:
            # If the next candidate is in but we're not, we'll catch
            # it on the next loop iteration — no early exit, just
            # let it walk through.
            pass
    del raw  # only used for logging if we add it later
    return majors
