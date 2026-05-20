"""Schema inspection tools for Odoo model discovery."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.server import (
    _get_auth_manager,
    _get_client,
    get_current_session_key,
)
from odoo_mcp_gateway.tools.crud import _validate_model

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


def register_schema_tools(server: FastMCP, gateway: GatewayContext) -> None:
    """Register schema inspection tools on the server."""

    @server.tool()
    async def list_models(
        filter: str = "",
        include_custom: bool = True,
    ) -> dict[str, Any]:
        """List available Odoo models. Optionally filter by keyword."""
        try:
            filter = filter.strip().lower()[:256]
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
            gate_error = await security_gate(gateway, "list_models", session_key)
            if gate_error:
                return {"error": gate_error}

            # Trigger discovery if needed
            if not gateway._models_discovered:
                await gateway.model_registry.discover(client)
                gateway._models_discovered = True

            if filter:
                # Keyword/substring search, then filter by access level
                models = gateway.model_registry.search_models(filter)
                accessible = set(
                    m.name
                    for m in gateway.model_registry.get_accessible_models(
                        is_admin=is_admin,
                    )
                )
                models = [m for m in models if m.name in accessible]
            else:
                models = gateway.model_registry.get_accessible_models(
                    is_admin=is_admin,
                )

            if not include_custom:
                models = [m for m in models if not m.is_custom]

            result = [
                {
                    "model": m.name,
                    "description": m.description,
                    "is_custom": m.is_custom,
                    "access_level": m.access_level.value,
                }
                for m in models
            ]

            return {"models": result, "count": len(result)}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error listing models")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def get_model_fields(
        model: str,
        field_filter: list[str] | str | None = None,
        include_readonly: bool = True,
    ) -> dict[str, Any]:
        """Get field definitions for an Odoo model.

        ``field_filter`` accepts THREE shapes:

        * ``None`` or empty string → no filter; every accessible field
          is returned.
        * Single string without commas (e.g. ``"email"``) → substring
          match against both the field name and its label (legacy
          behaviour preserved for back-compat).
        * Comma-separated string (e.g. ``"name,phone,email"``) OR a
          ``list[str]`` of names → EXACT field-name match. Each token
          is stripped + lowercased. Tokens that don't exist on the
          model are silently dropped — the response contains the
          subset that does exist; no error is raised.

        The filter composes WITH (intersects) the RBAC/restriction
        layer: a field that RBAC redacts is never returned, even if
        the caller explicitly named it. This preserves the two-layer
        security contract.
        """
        try:
            model = _validate_model(model)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
            gate_error = await security_gate(gateway, "get_model_fields", session_key)
            if gate_error:
                return {"error": gate_error}

            # Check model restrictions
            restriction_msg = gateway.restrictions.check_model_access(
                model,
                "read",
                is_admin,
            )
            if restriction_msg:
                return {"error": restriction_msg}

            fields = await gateway.field_inspector.get_fields(client, model)

            # Apply RBAC field filtering
            redact_fields = gateway.rbac.get_visible_fields(
                model,
                user_groups,
                is_admin,
            )

            # Normalise the filter into one of:
            #   * ``None``                 → no filter
            #   * ``("substring", str)``   → legacy single-token match
            #   * ``("names", set[str])``  → exact-name list match
            #
            # The UAT MED-1 finding caught the previous code path: a CSV
            # like ``"name,phone,email,is_company,country_id"`` was
            # substring-matched against every field name AND every
            # field label. No field NAME or label contains a comma, so
            # every field was filtered out and the response was empty.
            # We now detect the list shape (literal list, or string
            # containing a comma) and switch to exact-name matching.
            filter_mode: tuple[str, Any] | None = None
            if isinstance(field_filter, list):
                names = {
                    str(n).strip().lower()
                    for n in field_filter
                    if isinstance(n, str) and n.strip()
                }
                if names:
                    filter_mode = ("names", names)
            elif isinstance(field_filter, str):
                raw = field_filter.strip()
                if raw:
                    if "," in raw:
                        names = {
                            tok.strip().lower()
                            for tok in raw.split(",")
                            if tok.strip()
                        }
                        if names:
                            filter_mode = ("names", names)
                    else:
                        # Cap pathological single-token inputs at 256 chars
                        # to match the prior bound and avoid quadratic
                        # substring scans on absurd input.
                        filter_mode = ("substring", raw.lower()[:256])

            result: dict[str, Any] = {}
            for fname, finfo in fields.items():
                if not include_readonly and finfo.readonly:
                    continue
                if filter_mode is not None:
                    mode, value = filter_mode
                    if mode == "substring":
                        if (
                            value not in fname.lower()
                            and value not in (finfo.string or "").lower()
                        ):
                            continue
                    else:  # mode == "names"
                        if fname.lower() not in value:
                            continue
                # If redact_fields is not None, it contains fields to hide.
                # RBAC redaction wins over an explicit caller-supplied name:
                # the two-layer security contract requires it.
                if redact_fields is not None and fname in redact_fields:
                    continue

                result[fname] = {
                    "type": finfo.field_type,
                    "string": finfo.string,
                    "required": finfo.required,
                    "readonly": finfo.readonly,
                    "relation": finfo.relation,
                    "help": finfo.help_text,
                }

            return {"fields": result, "model": model, "count": len(result)}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error getting model fields")
            return {"error": gateway.sanitize_error(e)}
