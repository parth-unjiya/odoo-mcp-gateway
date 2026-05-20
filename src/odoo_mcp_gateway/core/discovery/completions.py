"""MCP completion handler — model / field / action discovery (ADR-009).

The MCP spec's ``completion/complete`` request lets clients ask the
server "what are the valid values for this argument as the user is
typing?" The SDK currently surfaces two reference types:

* :class:`PromptReference` — the user is filling in a prompt's
  argument (e.g. our ``analyze_model`` prompt has a ``model``
  argument).
* :class:`ResourceTemplateReference` — the user is constructing a
  resource URI (e.g. ``odoo://models/{model_name}``).

For both, we look at the argument NAME and route to a domain-aware
matcher:

* ``model`` / ``model_name`` → completes from
  ``gateway.model_registry`` filtered through restrictions (so
  blocked models never leak).
* ``record_id`` / ``record_ids`` → returns a syntax hint, since we
  can't enumerate IDs cheaply without a search round-trip and we
  don't have the model context here.
* ``method`` / ``action`` → completes from
  ``model_access.yaml``'s ``allowed_methods`` map for the resolved
  model (if context provides one).

The handler is intentionally cheap — no Odoo round-trip during
typing. Model names come from the in-process ``ModelRegistry``
(populated on first ``list_models`` / ``get_model_fields`` call).
Field names would require knowing the model AND making a
``fields_get`` call; we defer that to a Sprint 5 enhancement when
the MCP spec stabilises a ``ToolReference`` for completions.

The MCP spec caps completion responses at 100 values — we cap at 50
to keep the wire payload small and let typeahead refine.

Why this matters: Odoo's biggest discoverability failure is "what's
the right model name?" Users guess ``customer`` instead of
``res.partner``, ``invoice`` instead of ``account.move``. Live
completions kill that class of guess.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

# Wire-payload cap (MCP spec allows up to 100; we use 50 for tight
# responses and to keep the LLM context budget small).
MAX_COMPLETIONS = 50

# Argument names that should complete from the model registry.
_MODEL_ARG_NAMES = frozenset(
    {
        "model",
        "model_name",
        "modelname",
        "_model",
    }
)

# Argument names that look like record IDs — we return a syntax hint
# rather than enumerating live records (which would require an Odoo
# round-trip on every keystroke).
_RECORD_ID_ARG_NAMES = frozenset(
    {
        "record_id",
        "record_ids",
        "id",
        "ids",
    }
)

# Argument names for method / workflow actions.
_METHOD_ARG_NAMES = frozenset(
    {
        "method",
        "action",
    }
)


def _complete_model_name(
    gateway: GatewayContext,
    prefix: str,
    is_admin: bool,
) -> list[str]:
    """Return matching model names, restriction-filtered.

    The strategy:
    1. Pull the accessible model list from ``ModelRegistry``.
    2. If the registry hasn't been populated yet (no
       ``list_models`` call has run), fall back to the YAML
       ``stock_models`` + ``custom_models`` from
       ``gateway.gateway_config.model_access``.
    3. Filter by prefix (case-insensitive); rank exact-prefix
       matches above substring matches.
    4. Drop anything that ``restrictions.check_model_access(model,
       'read', is_admin)`` would reject — completion must NOT
       suggest models the caller can't actually use.
    """
    registry = gateway.model_registry
    accessible = registry.get_accessible_models(is_admin=is_admin)
    candidates = [m.name for m in accessible]

    # If the registry hasn't been discovered yet, fall back to the
    # YAML allowlist. This avoids "you have to call list_models
    # once before completions work" UX trap.
    if not candidates:
        ma = gateway.gateway_config.model_access
        seen: set[str] = set()
        for collection in (ma.stock_models, ma.custom_models):
            for _category, models in collection.items():
                for name in models:
                    if name and name not in seen:
                        seen.add(name)
                        candidates.append(name)

    # Filter by prefix (case-insensitive).
    p = prefix.lower()
    prefix_matches: list[str] = []
    substring_matches: list[str] = []
    for name in candidates:
        lname = name.lower()
        if lname.startswith(p):
            prefix_matches.append(name)
        elif p and p in lname:
            substring_matches.append(name)

    # Apply read-access check so blocked models never appear.
    blocked = []
    filtered_prefix: list[str] = []
    filtered_substring: list[str] = []
    for name in prefix_matches:
        if gateway.restrictions.check_model_access(name, "read", is_admin):
            blocked.append(name)
        else:
            filtered_prefix.append(name)
    for name in substring_matches:
        if gateway.restrictions.check_model_access(name, "read", is_admin):
            blocked.append(name)
        else:
            filtered_substring.append(name)
    if blocked:
        logger.debug(
            "Suppressed %d restricted models from completion: %s",
            len(blocked),
            blocked[:5],
        )

    # Prefix matches first, then substrings; both sorted alphabetically
    # so the order is stable across requests.
    filtered_prefix.sort()
    filtered_substring.sort()
    combined = filtered_prefix + filtered_substring
    return combined[:MAX_COMPLETIONS]


def _complete_method_name(
    gateway: GatewayContext,
    prefix: str,
    model_context: str | None,
) -> list[str]:
    """Complete a model method name from the YAML allowed_methods map.

    When ``model_context`` is None (the caller hasn't filled in
    ``model`` yet), we return the union of methods across every model
    — useful for discoverability but lower precision. When a model is
    known, we narrow to that model's allowlist.
    """
    allowed_methods = gateway.gateway_config.model_access.allowed_methods
    candidates: set[str] = set()
    if model_context and model_context in allowed_methods:
        candidates.update(allowed_methods[model_context])
    else:
        # No model context — surface every allowed method as a
        # global discoverability hint. Duplicates collapse via set.
        for methods in allowed_methods.values():
            candidates.update(methods)

    p = prefix.lower()
    sorted_candidates = sorted(c for c in candidates if not p or p in c.lower())
    return sorted_candidates[:MAX_COMPLETIONS]


def _record_id_hint() -> list[str]:
    """Return a single 'syntax hint' completion for record_id args."""
    # Returning one informational suggestion is better than nothing
    # — the client renders it in the typeahead and the user sees the
    # expected shape immediately.
    return ["1"]


def build_completion_handler(
    gateway: GatewayContext,
) -> Any:
    """Return an async completion handler bound to *gateway*.

    Caller wires the result via ``@server.completion()`` (decorator
    style) or ``server._mcp_server.completion()(handler)``.
    """

    async def handle_completion(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion | None:
        try:
            arg_name = argument.name.lower()
            prefix = argument.value or ""

            # Resolve admin status from the active session if
            # available; default to non-admin (the safer assumption).
            is_admin = False
            if gateway.auth_managers:
                mgr = next(iter(gateway.auth_managers.values()), None)
                if mgr is not None and mgr.auth_result is not None:
                    is_admin = bool(mgr.auth_result.is_admin)

            # 1) Model-name arguments.
            if arg_name in _MODEL_ARG_NAMES:
                values = _complete_model_name(gateway, prefix, is_admin)
                return Completion(values=values, total=len(values), hasMore=False)

            # 2) Method / action arguments. If the resource URI has
            # already resolved a ``model_name``, use it as context.
            if arg_name in _METHOD_ARG_NAMES:
                model_ctx: str | None = None
                if context is not None and context.arguments:
                    for key in ("model", "model_name", "modelname"):
                        if key in context.arguments:
                            model_ctx = context.arguments[key]
                            break
                values = _complete_method_name(gateway, prefix, model_ctx)
                return Completion(values=values, total=len(values), hasMore=False)

            # 3) Record-id arguments — syntax hint only.
            if arg_name in _RECORD_ID_ARG_NAMES:
                values = _record_id_hint()
                return Completion(values=values, total=len(values), hasMore=False)

            # 4) Unknown argument — return empty (the spec allows
            # a "no completions" answer; clients fall back to free
            # text input).
            return None

        except Exception:
            logger.exception(
                "Completion handler raised — returning empty completion to "
                "preserve UX (don't let typeahead crash the client)"
            )
            return None

    return handle_completion
