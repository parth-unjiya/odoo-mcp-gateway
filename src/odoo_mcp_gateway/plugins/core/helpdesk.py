"""Helpdesk domain plugin: ticket management."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.plugins.base import OdooPlugin
from odoo_mcp_gateway.plugins.core.helpers import (
    check_plugin_modules,
    check_security_gate,
    format_model_error,
    get_auth_info,
    get_client,
    get_uid,
)

# The model name varies between Odoo installations.  Stock Odoo
# ships ``helpdesk.ticket``; the popular ``odoo_website_helpdesk``
# module on the OCA replaces it with ``ticket.helpdesk``.  The
# plugin's ``ticket_model`` attribute is the static default;
# operators flip it via ``model_access.yaml::plugin_overrides`` and
# the registry exposes the resolved value through
# ``PluginInfo.effective_model_name``.

_VALID_PRIORITIES = frozenset({"0", "1", "2", "3"})

# Stage name filter: word chars, spaces, hyphens; length 1-64.
# Prevents passing arbitrary strings into the dotted-path domain filter
# on `stage_id.name`.
_STATE_FILTER_RE = re.compile(r"^[\w \-]{1,64}$")


class HelpdeskPlugin(OdooPlugin):
    """Provides MCP tools for helpdesk: tickets, teams, priorities.

    v0.3.3 follow-up MED-3: installations running a custom helpdesk
    module (e.g. ``odoo_website_helpdesk`` which ships
    ``ticket.helpdesk`` instead of stock ``helpdesk.ticket``) can opt
    into compatibility by configuring
    ``model_access.yaml::plugin_overrides.helpdesk``::

        plugin_overrides:
          helpdesk:
            accept_modules: ["helpdesk", "odoo_website_helpdesk"]
            accept_models:  ["helpdesk.ticket", "ticket.helpdesk"]

    The registry merges these into ``required_*`` with OR semantics
    and exposes the resolved model name as ``PluginInfo.effective_model_name``.
    The tools below read that value through ``_resolve_ticket_model()``
    so a single deployment can stay on whichever module Odoo actually
    has installed without forking the plugin.
    """

    plugin_sdk_version = ">=1.0,<2.0"

    # Default model name — overridden at runtime when an operator
    # configures ``plugin_overrides.helpdesk.accept_models``.
    ticket_model: str = "helpdesk.ticket"

    @property
    def name(self) -> str:
        return "helpdesk"

    @property
    def description(self) -> str:
        return "Helpdesk tools: tickets, teams, priorities"

    @property
    def required_odoo_modules(self) -> list[str]:
        return ["helpdesk"]

    @property
    def required_models(self) -> list[str]:
        return ["helpdesk.ticket"]

    def register(self, server: FastMCP, context: Any) -> None:
        """Register helpdesk tools on the MCP server."""
        _plugin_name = self.name
        _required_models = self.required_models

        def _resolve_ticket_model() -> str:
            """Resolve the effective ticket model for THIS deployment.

            Order of preference: the registry's ``effective_model_name``
            (set by ``set_plugin_overrides`` from YAML) -> the plugin's
            class-level ``ticket_model`` default. The helper is called
            lazily inside each tool so an operator changing the YAML
            and re-loading the gateway picks up the new value without
            re-registration. Defensive against MagicMock-flavoured test
            contexts: any non-string ``effective_model_name`` falls
            back to the static default.
            """
            registry = getattr(context, "plugin_registry", None)
            if registry is None:
                return self.ticket_model
            try:
                info = registry.get_plugin(_plugin_name)
            except Exception:
                return self.ticket_model
            if info is None:
                return self.ticket_model
            effective = getattr(info, "effective_model_name", None)
            if isinstance(effective, str) and effective:
                return effective
            return self.ticket_model

        @server.tool()
        async def get_my_tickets(
            state: str | None = None,
            priority: str | None = None,
            limit: int = 20,
        ) -> dict[str, Any]:
            """Get helpdesk tickets assigned to the current user.

            Args:
                state: Filter by stage name (optional)
                priority: Filter by priority (0=Low, 1=Medium, 2=High, 3=Urgent)
                limit: Max records (default 20)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            module_error = check_plugin_modules(context, _plugin_name, _required_models)
            if module_error:
                return {"error": module_error}

            gate_error = await check_security_gate(context, "get_my_tickets")
            if gate_error:
                return {"error": gate_error}

            if priority and priority not in _VALID_PRIORITIES:
                return {"error": f"Invalid priority: {priority!r}"}

            if state is not None and not _STATE_FILTER_RE.match(state):
                return {"error": "Invalid state filter format"}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            ticket_model = _resolve_ticket_model()
            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    ticket_model, "read", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                domain: list[Any] = [["user_id", "=", uid]]
                if state:
                    domain.append(["stage_id.name", "=", state])
                if priority:
                    domain.append(["priority", "=", priority])

                records = await client.execute_kw(
                    ticket_model,
                    "search_read",
                    [domain],
                    {
                        "fields": [
                            "name",
                            "description",
                            "stage_id",
                            "priority",
                            "team_id",
                            "partner_id",
                            "create_date",
                        ],
                        "limit": min(max(limit, 1), 100),
                        "order": "priority desc, create_date desc",
                    },
                )

                filtered = context.rbac.filter_response_fields(
                    records, ticket_model, user_groups, is_admin
                )
                if isinstance(filtered, list):
                    records = filtered

                return {"tickets": records, "count": len(records)}
            except Exception as e:
                model_err = format_model_error(
                    ticket_model, e, alternate_models=["ticket.helpdesk"]
                )
                if model_err:
                    return {"error": model_err}
                sanitised = context.sanitize_error(e)
                # UAT v0.3.3 LOW (Odoo 19): an unhandled non-model-error
                # path used to return the bare sanitiser fallback
                # ``"An unexpected error occurred"``. Direct
                # ``search_read`` on ``helpdesk.ticket`` works for the
                # same user, so the plugin-side wrapping is the surface
                # to soften. Replace the opaque message with a
                # friendly hint pointing the caller at the supported
                # workaround (direct ``search_read``) and surface an
                # empty list so the wire shape stays consistent.
                if sanitised == "An unexpected error occurred":
                    return {
                        "tickets": [],
                        "count": 0,
                        "error": (
                            "Could not resolve helpdesk tickets for your "
                            "user. The plugin wrapper failed for an "
                            "unspecified reason."
                        ),
                        "hint": (
                            "Try calling search_read on "
                            f"{ticket_model!r} directly with domain "
                            "[['user_id', '=', <your_uid>]] — this "
                            "bypasses the plugin wrapper."
                        ),
                    }
                return {"error": sanitised}

        @server.tool()
        async def create_ticket(
            name: str,
            description: str = "",
            team_id: int | None = None,
            priority: str = "1",
        ) -> dict[str, Any]:
            """Create a new helpdesk ticket.

            Args:
                name: Ticket subject/title
                description: Detailed description
                team_id: Helpdesk team ID (optional)
                priority: Priority level (0=Low, 1=Medium, 2=High, 3=Urgent)
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            module_error = check_plugin_modules(context, _plugin_name, _required_models)
            if module_error:
                return {"error": module_error}

            gate_error = await check_security_gate(context, "create_ticket")
            if gate_error:
                return {"error": gate_error}

            if priority and priority not in _VALID_PRIORITIES:
                return {"error": f"Invalid priority: {priority!r}"}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            ticket_model = _resolve_ticket_model()
            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    ticket_model, "create", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                values: dict[str, Any] = {
                    "name": name,
                    "user_id": uid,
                    "priority": priority,
                }
                if description:
                    values["description"] = description
                if team_id is not None:
                    if team_id <= 0:
                        return {"error": "team_id must be a positive integer"}
                    values["team_id"] = team_id

                # Check blocked write fields
                for field_name in values:
                    field_msg = context.restrictions.check_field_write(
                        ticket_model, field_name, is_admin
                    )
                    if field_msg:
                        return {"error": field_msg}

                sanitized = context.rbac.sanitize_write_values(
                    values, ticket_model, user_groups, is_admin
                )
                if isinstance(sanitized, dict):
                    # If RBAC stripped user_id, refuse to create an unassigned
                    # ticket silently — bail out with a clear permission error.
                    if "user_id" in values and "user_id" not in sanitized:
                        return {
                            "error": (
                                "Cannot create ticket: your role lacks "
                                "permission to assign user_id. Contact an "
                                "administrator."
                            )
                        }
                    values = sanitized

                ticket_id = await client.execute_kw(
                    ticket_model,
                    "create",
                    [values],
                )
                return {
                    "status": "created",
                    "ticket_id": ticket_id,
                    "name": name,
                    "priority": priority,
                }
            except Exception as e:
                model_err = format_model_error(
                    ticket_model, e, alternate_models=["ticket.helpdesk"]
                )
                return {"error": model_err or context.sanitize_error(e)}

        @server.tool()
        async def update_ticket_stage(
            ticket_id: int,
            stage_id: int,
        ) -> dict[str, Any]:
            """Move a helpdesk ticket to a different stage.

            Args:
                ticket_id: The ticket ID to update
                stage_id: The target stage ID
            """
            client = get_client(context)
            if client is None:
                return {"error": "Not authenticated"}

            module_error = check_plugin_modules(context, _plugin_name, _required_models)
            if module_error:
                return {"error": module_error}

            if ticket_id <= 0:
                return {"error": "ticket_id must be a positive integer"}
            if stage_id <= 0:
                return {"error": "stage_id must be a positive integer"}

            gate_error = await check_security_gate(context, "update_ticket_stage")
            if gate_error:
                return {"error": gate_error}

            uid = get_uid(context)
            if uid == 0:
                return {"error": "Not authenticated"}

            ticket_model = _resolve_ticket_model()
            try:
                is_admin, user_groups = get_auth_info(context)

                restriction_msg = context.restrictions.check_model_access(
                    ticket_model, "write", is_admin
                )
                if isinstance(restriction_msg, str):
                    return {"error": restriction_msg}

                # UAT HIGH-1: previously the lookup added
                # ``["user_id", "=", uid]`` for non-admin callers, masking
                # any ticket the user could read via ``search_read`` (e.g.
                # a helpdesk_manager managing tickets they didn't own) as
                # "Ticket not found". That contradicted Odoo's own ACL
                # surface and gave a confusing 404-style error to a user
                # whose role legitimately allowed the operation.
                #
                # We now defer entirely to Odoo's ``ir.rule`` for read
                # visibility: if ``search_read`` returns the record, the
                # caller has read access. If the subsequent ``write``
                # fails due to a write-ACL restriction, Odoo's error is
                # surfaced verbatim (post-sanitisation) rather than
                # masked as a not-found.
                domain: list[Any] = [["id", "=", ticket_id]]
                tickets = await client.execute_kw(
                    ticket_model,
                    "search_read",
                    [domain],
                    {"fields": ["id", "name", "stage_id"], "limit": 1},
                )
                if not tickets:
                    return {"error": "Ticket not found"}

                ticket = tickets[0]
                old_stage = ticket.get("stage_id")

                values = {"stage_id": stage_id}

                # Check blocked write fields
                for field_name in values:
                    field_msg = context.restrictions.check_field_write(
                        ticket_model, field_name, is_admin
                    )
                    if field_msg:
                        return {"error": field_msg}

                sanitized = context.rbac.sanitize_write_values(
                    values, ticket_model, user_groups, is_admin
                )
                if isinstance(sanitized, dict):
                    values = sanitized

                await client.execute_kw(
                    ticket_model,
                    "write",
                    [[ticket_id], values],
                )
                return {
                    "status": "updated",
                    "ticket_id": ticket_id,
                    "ticket_name": ticket["name"],
                    "old_stage": old_stage,
                    "new_stage_id": stage_id,
                }
            except Exception as e:
                model_err = format_model_error(
                    ticket_model, e, alternate_models=["ticket.helpdesk"]
                )
                return {"error": model_err or context.sanitize_error(e)}
