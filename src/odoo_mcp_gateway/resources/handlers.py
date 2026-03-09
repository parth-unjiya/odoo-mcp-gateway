"""MCP Resource handlers for Odoo data access via URIs."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security import security_gate

logger = logging.getLogger(__name__)

_MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


async def _resource_gate(ctx: Any, resource_name: str) -> str | None:
    """Run security gate for resource access (rate limit + RBAC + audit)."""
    if not hasattr(ctx, "auth_managers") or not ctx.auth_managers:
        return None
    session_key = next(iter(ctx.auth_managers.keys()), "default")
    return await security_gate(ctx, f"resource:{resource_name}", session_key)


def _validate_model_name(name: str) -> str:
    """Validate and normalize model name."""
    name = name.strip().lower()
    if not name or len(name) > 128 or not _MODEL_RE.match(name):
        raise ValueError(f"Invalid model name: {name!r}")
    return name


def register_resources(server: FastMCP, get_context: Any) -> None:
    """Register all MCP resources on the server.

    get_context is a callable that returns the GatewayContext.
    Resources use it to access the model registry, field inspector, and client.
    """

    @server.resource("odoo://models")
    async def list_models_resource() -> str:
        """List all accessible Odoo models.

        Returns JSON array of model objects with name, description,
        is_custom, and access_level.
        """
        try:
            ctx = get_context()
            gate_err = await _resource_gate(ctx, "list_models")
            if gate_err:
                return json.dumps({"error": gate_err})
            models = ctx.model_registry.get_accessible_models(is_admin=_is_admin(ctx))
            result = [
                {
                    "model": m.name,
                    "description": m.description,
                    "is_custom": m.is_custom,
                    "access_level": m.access_level.value,
                }
                for m in models
            ]
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({"error": f"Failed to list models: {type(e).__name__}"})

    @server.resource("odoo://models/{model_name}")
    async def model_detail_resource(model_name: str) -> str:
        """Get detailed info about a specific model including fields.

        Returns JSON object with model info and field definitions.
        """
        ctx = get_context()
        gate_err = await _resource_gate(ctx, "model_detail")
        if gate_err:
            return json.dumps({"error": gate_err})
        try:
            model_name = _validate_model_name(model_name)
        except ValueError:
            return json.dumps({"error": "Invalid model name"})
        is_admin = _is_admin(ctx)

        # Enforce model access restrictions
        restriction_msg = ctx.restrictions.check_model_access(
            model_name, "read", is_admin
        )
        if restriction_msg:
            return json.dumps({"error": restriction_msg})

        model = ctx.model_registry.get_model(model_name)
        if model is None:
            return json.dumps({"error": f"Model '{model_name}' not found"})

        client = _get_client(ctx)
        if client is None:
            return json.dumps(
                {
                    "model": model_name,
                    "description": model.description,
                    "is_custom": model.is_custom,
                    "access_level": model.access_level.value,
                    "fields": "Login required to view fields",
                }
            )

        fields = await ctx.field_inspector.get_fields(client, model_name)
        field_data = {
            name: {
                "type": f.field_type,
                "string": f.string,
                "required": f.required,
                "readonly": f.readonly,
                "relation": f.relation,
            }
            for name, f in fields.items()
            if not f.is_binary
        }

        # Apply RBAC: hide sensitive field metadata
        auth_result = _get_auth_result(ctx)
        user_groups = auth_result.groups if auth_result else []
        redact_fields = ctx.rbac.get_visible_fields(model_name, user_groups, is_admin)
        if redact_fields:
            field_data = {k: v for k, v in field_data.items() if k not in redact_fields}

        return json.dumps(
            {
                "model": model_name,
                "description": model.description,
                "is_custom": model.is_custom,
                "access_level": model.access_level.value,
                "field_count": len(field_data),
                "fields": field_data,
            },
            indent=2,
        )

    @server.resource("odoo://record/{model_name}/{record_id}")
    async def record_resource(model_name: str, record_id: str) -> str:
        """Get a single record by model and ID.

        Returns JSON object with the record data.
        """
        ctx = get_context()
        gate_err = await _resource_gate(ctx, "record")
        if gate_err:
            return json.dumps({"error": gate_err})
        try:
            model_name = _validate_model_name(model_name)
        except ValueError:
            return json.dumps({"error": "Invalid model name"})
        is_admin = _is_admin(ctx)

        # Enforce model access restrictions
        restriction_msg = ctx.restrictions.check_model_access(
            model_name, "read", is_admin
        )
        if restriction_msg:
            return json.dumps({"error": restriction_msg})

        client = _get_client(ctx)
        if client is None:
            return json.dumps({"error": "Not authenticated. Call login first."})

        try:
            rid = int(record_id)
        except ValueError:
            return json.dumps({"error": f"Invalid record ID: {record_id}"})

        if rid <= 0:
            return json.dumps({"error": "Record ID must be positive"})

        try:
            records = await client.execute_kw(model_name, "read", [[rid]], {})
            if not records:
                return json.dumps({"error": f"Record {model_name}/{rid} not found"})

            # Apply RBAC field filtering
            auth_result = _get_auth_result(ctx)
            user_groups = auth_result.groups if auth_result else []
            filtered = ctx.rbac.filter_response_fields(
                records, model_name, user_groups, is_admin
            )
            record = filtered[0] if filtered else records[0]
            return json.dumps(record, indent=2, default=str)
        except Exception as e:
            logger.exception("Error reading record %s/%s", model_name, rid)
            return json.dumps({"error": f"Failed to read record: {type(e).__name__}"})

    @server.resource("odoo://schema/{model_name}")
    async def schema_resource(model_name: str) -> str:
        """Get the field schema for a model (cached).

        Returns JSON object mapping field names to their definitions.
        Useful for AI to understand model structure before querying.
        """
        ctx = get_context()
        gate_err = await _resource_gate(ctx, "schema")
        if gate_err:
            return json.dumps({"error": gate_err})
        try:
            model_name = _validate_model_name(model_name)
        except ValueError:
            return json.dumps({"error": "Invalid model name"})
        is_admin = _is_admin(ctx)

        # Enforce model access restrictions
        restriction_msg = ctx.restrictions.check_model_access(
            model_name, "read", is_admin
        )
        if restriction_msg:
            return json.dumps({"error": restriction_msg})

        client = _get_client(ctx)
        if client is None:
            return json.dumps({"error": "Not authenticated."})

        fields = await ctx.field_inspector.get_fields(client, model_name)
        important = ctx.field_inspector.get_important_fields(model_name, fields)

        schema: dict[str, Any] = {}
        for name, f in fields.items():
            entry: dict[str, Any] = {
                "type": f.field_type,
                "label": f.string,
                "required": f.required,
            }
            if f.relation:
                entry["relation"] = f.relation
            if f.selection:
                entry["options"] = f.selection
            if f.help_text:
                entry["help"] = f.help_text
            entry["important"] = name in important
            schema[name] = entry

        # Apply RBAC: hide sensitive field metadata
        auth_result = _get_auth_result(ctx)
        user_groups = auth_result.groups if auth_result else []
        redact_fields = ctx.rbac.get_visible_fields(model_name, user_groups, is_admin)
        if redact_fields:
            schema = {k: v for k, v in schema.items() if k not in redact_fields}

        return json.dumps(
            {
                "model": model_name,
                "total_fields": len(schema),
                "important_fields": important,
                "fields": schema,
            },
            indent=2,
        )

    @server.resource("odoo://categories")
    async def categories_resource() -> str:
        """List available model categories with counts.

        Returns JSON object mapping category names to model counts.
        """
        try:
            ctx = get_context()
            gate_err = await _resource_gate(ctx, "categories")
            if gate_err:
                return json.dumps({"error": gate_err})
            from odoo_mcp_gateway.core.discovery.suggestions import (
                ModelSuggestions,
            )

            suggestions = ModelSuggestions(ctx.model_registry)
            categories = suggestions.get_categories(is_admin=_is_admin(ctx))
            return json.dumps(categories, indent=2)
        except Exception as e:
            err = f"Failed to list categories: {type(e).__name__}"
            return json.dumps({"error": err})


def _get_client(ctx: Any) -> Any:
    """Get the active authenticated client from context."""
    if not ctx.auth_managers:
        return None
    auth_mgr = next(iter(ctx.auth_managers.values()))
    try:
        return auth_mgr.get_active_client()
    except Exception:
        return None


def _get_auth_result(ctx: Any) -> Any:
    """Get the current auth result from context."""
    if not ctx.auth_managers:
        return None
    auth_mgr = next(iter(ctx.auth_managers.values()))
    return getattr(auth_mgr, "auth_result", None)


def _is_admin(ctx: Any) -> bool:
    """Check if current user is admin."""
    result = _get_auth_result(ctx)
    if result is None:
        return False
    return getattr(result, "is_admin", False)
