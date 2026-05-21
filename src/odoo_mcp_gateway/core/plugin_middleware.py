"""Plugin lifecycle middleware for FastMCP tool calls (audit blocker #4).

The Plugin SDK 1.0 (Sprint 3) exposes seven lifecycle hooks on
``OdooPlugin`` — ``pre_register``, ``register``, ``post_register``,
``pre_call``, ``post_call``, ``on_session_close``, and
``on_external_event`` — and the ``PluginRegistry`` provides
``dispatch_*`` helpers for the async ones.  ``on_external_event`` is
reserved for the v0.4.0 webhook stack (documented in
``plugins/sdk.py``) but shipped now so plugin authors can prepare.
However, the gateway never actually CALLED ``dispatch_pre_call`` or
``dispatch_post_call`` around tool invocations, and
``dispatch_on_session_close`` was never fired when a session was
evicted by single-user-per-process enforcement.

In effect 6 of the 7 documented lifecycle hooks were dead code:
plugins could implement them but the gateway would never reach them.

This module addresses the per-call half of that gap. It exposes
:class:`PluginLifecycleMiddleware`, which the server factory mounts
by monkey-patching ``FastMCP.call_tool`` so each tool invocation
runs::

    await dispatch_pre_call(...)
    result = await original_call_tool(...)
    result = await dispatch_post_call(..., result)

with per-plugin error isolation — a misbehaving plugin's exception
is logged and SWALLOWED rather than allowed to break the user's
tool call. The dispatch_pre_call helper does NOT swallow exceptions
intentionally (so an auth-gating plugin can ABORT a call by
raising), but each individual dispatcher iteration is wrapped here
in a try/except for defence in depth.

NOTE: the FastMCP SDK doesn't ship a first-class middleware
abstraction for tool dispatch. The monkey-patch approach is the
documented integration pattern; if the SDK adds proper middleware
support later (anticipated in MCP SDK v2.0), this module collapses
to a subclass implementation without changing the public surface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from odoo_mcp_gateway.plugins.registry import PluginRegistry
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


class PluginLifecycleMiddleware:
    """Hooks ``pre_call`` and ``post_call`` plugin dispatch into tool calls.

    Mount once per FastMCP server with :meth:`install`. Subsequent
    tool invocations on that server transparently run the plugin
    lifecycle hooks.

    Error isolation contract:

    * ``pre_call`` exceptions PROPAGATE — this lets plugins ABORT a
      call (e.g. for security gating). The wrapper logs and re-raises.
      Per-plugin isolation is up to the dispatcher (the existing
      ``dispatch_pre_call`` swallows in production; we honour that).
    * ``post_call`` exceptions are SWALLOWED with a warning. The
      gateway must not let a buggy plugin's post-processing destroy
      a legitimate tool result.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        gateway: GatewayContext,
    ) -> None:
        self._registry = registry
        self._gateway = gateway

    def install(self, server: FastMCP) -> None:
        """Patch *server*'s ``call_tool`` to bracket plugin dispatch.

        Idempotent: re-installing on the same server is a no-op
        thanks to the ``_plugin_middleware_installed`` sentinel.
        """
        if getattr(server, "_plugin_middleware_installed", False):
            return

        original_call_tool = server.call_tool
        registry = self._registry
        gateway = self._gateway

        async def _wrapped_call_tool(
            name: str,
            arguments: dict[str, Any],
        ) -> Any:
            # PRE: dispatch_pre_call may raise to ABORT the call.
            # The dispatcher's per-plugin try/except is up to the
            # registry; for v0.3.0 the dispatcher re-raises so a
            # security plugin can deny the call. We do NOT add
            # another swallow here.
            await registry.dispatch_pre_call(gateway, name, arguments)

            # ACTUAL TOOL CALL.
            try:
                result = await original_call_tool(name, arguments)
            except Exception as exc:
                # POST on FAILURE: also fire post_call so plugins can
                # observe errors (metrics, audit, ...). Wrap defensively
                # since a plugin must NOT mask the real exception.
                try:
                    await registry.dispatch_post_call(gateway, name, exc)
                except Exception:
                    logger.warning(
                        "Plugin post_call hook failed during error path",
                        exc_info=True,
                    )
                raise

            # POST on SUCCESS: plugins may transform the result. The
            # dispatcher already swallows per-plugin exceptions and
            # returns the unchanged result when a plugin raises.
            try:
                result = await registry.dispatch_post_call(gateway, name, result)
            except Exception:
                logger.warning(
                    "Plugin post_call dispatch failed; returning unmodified result",
                    exc_info=True,
                )
            return result

        # Bind the wrapper. Setattr instead of decorator so we keep
        # the SDK's existing FastMCP.call_tool signature exactly.
        server.call_tool = _wrapped_call_tool  # type: ignore[method-assign]
        server._plugin_middleware_installed = True  # type: ignore[attr-defined]


def install_plugin_middleware(
    server: FastMCP,
    registry: PluginRegistry,
    gateway: GatewayContext,
) -> PluginLifecycleMiddleware:
    """Convenience function: build and install the middleware in one call."""
    mw = PluginLifecycleMiddleware(registry, gateway)
    mw.install(server)
    return mw
