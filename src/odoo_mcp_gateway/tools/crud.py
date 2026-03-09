"""Generic CRUD tools that work with any Odoo model."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.server import _get_auth_manager, _get_client
from odoo_mcp_gateway.utils.domain_builder import (
    DomainValidationError,
    validate_domain,
)

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

_MAX_LIMIT = 500
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")

_MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_MAX_MODEL_LEN = 128


def _validate_model(model: str) -> str:
    """Validate and normalize a model name. Raises ValueError if invalid."""
    model = model.strip().lower()
    if not model or len(model) > _MAX_MODEL_LEN or not _MODEL_RE.match(model):
        raise ValueError(f"Invalid model name: {model!r}")
    return model


_WRITE_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_METHOD_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_MAX_METHOD_LEN = 128


def _validate_method(method: str) -> str:
    """Validate and normalize a method name. Raises ValueError if invalid."""
    method = method.strip().lower()
    if not method or len(method) > _MAX_METHOD_LEN or not _METHOD_RE.match(method):
        raise ValueError(f"Invalid method name: {method!r}")
    return method


_AGG_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z]+)?$")


def _validate_fields(fields: list[str]) -> list[str]:
    """Validate field names. Returns cleaned list or raises ValueError."""
    if not fields:
        return fields
    for f in fields:
        if not _FIELD_RE.match(f):
            raise ValueError(f"Invalid field name: {f!r}")
    return fields


def _validate_agg_fields(fields: list[str]) -> list[str]:
    """Validate aggregate field names (e.g. 'amount_total:sum')."""
    if not fields:
        return fields
    for f in fields:
        if not _AGG_FIELD_RE.match(f):
            raise ValueError(f"Invalid field name: {f!r}")
    return fields


_MAX_ARG_DEPTH = 10
_MAX_ARG_SIZE = 100_000  # chars when serialized


def _validate_method_args(args: list[Any], kwargs: dict[str, Any]) -> None:
    """Check args aren't excessively deep or large."""
    try:
        serialized = json.dumps(args) + json.dumps(kwargs)
    except TypeError as e:
        raise ValueError(f"Method arguments contain non-serializable types: {e}") from e
    if len(serialized) > _MAX_ARG_SIZE:
        raise ValueError("Method arguments too large")

    def _check_depth(obj: Any, depth: int = 0) -> None:
        if depth > _MAX_ARG_DEPTH:
            raise ValueError("Method arguments too deeply nested")
        if isinstance(obj, dict):
            for v in obj.values():
                _check_depth(v, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _check_depth(v, depth + 1)

    _check_depth(args)
    _check_depth(kwargs)


_MAX_VALUES_SIZE = 100_000  # chars when serialized
_MAX_VALUES_DEPTH = 5
_MAX_STRING_VALUE_LEN = 65_536


def _validate_write_values(values: dict[str, Any]) -> None:
    """Validate write values size and depth. Raises ValueError if invalid."""
    try:
        serialized = json.dumps(values)
    except TypeError as e:
        raise ValueError(f"Values contain non-serializable types: {e}") from e
    if len(serialized) > _MAX_VALUES_SIZE:
        raise ValueError("Write values too large")

    def _check(obj: Any, depth: int = 0) -> None:
        if depth > _MAX_VALUES_DEPTH:
            raise ValueError("Write values too deeply nested")
        if isinstance(obj, dict):
            for v in obj.values():
                _check(v, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _check(v, depth + 1)
        elif isinstance(obj, str) and len(obj) > _MAX_STRING_VALUE_LEN:
            raise ValueError("String value too long (max 64KB)")

    _check(values)


# ORM methods that are already exposed through dedicated CRUD tools.
# Blocking them in execute_method prevents bypassing field-level checks.
_BLOCKED_ORM_METHODS = frozenset(
    {
        "read",
        "search",
        "search_read",
        "search_count",
        "write",
        "create",
        "unlink",
        "copy",
        "export_data",
        "import_data",
        "load",
        "name_create",
        "read_group",
        "name_search",
        "fields_get",
        "web_read",
        "web_search_read",
        "web_read_group",
        "browse",
        "exists",
        "mapped",
        "filtered",
        "default_get",
        "onchange",
        "name_get",
        "fields_view_get",
        "check_access_rights",
        "check_access_rule",
    }
)


def _validate_order(order: str | None) -> str:
    """Validate and reconstruct a clean ORDER BY clause."""
    if not order or not order.strip():
        return ""
    clean_parts = []
    for part in order.split(","):
        tokens = part.strip().split()
        if not tokens:
            continue
        if len(tokens) > 2:
            raise ValueError(f"Invalid order clause: {part.strip()!r}")
        field = tokens[0]
        if field.count(".") > 3:
            raise ValueError(f"Field traversal too deep in order: {field!r}")
        if not _FIELD_RE.match(field):
            raise ValueError(f"Invalid field in order: {field!r}")
        direction = "asc"
        if len(tokens) == 2:
            direction = tokens[1].lower()
            if direction not in ("asc", "desc"):
                raise ValueError(f"Invalid sort direction: {tokens[1]!r}")
        clean_parts.append(f"{field} {direction}")
    return ", ".join(clean_parts)


def register_crud_tools(server: FastMCP, gateway: GatewayContext) -> None:
    """Register CRUD and method execution tools on the server."""

    @server.tool()
    async def search_read(
        model: str,
        domain: list[Any] | None = None,
        fields: list[str] | None = None,
        limit: int = 80,
        offset: int = 0,
        order: str | None = None,
    ) -> dict[str, Any]:
        """Search and read records from any Odoo model."""
        try:
            model = _validate_model(model)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "search_read", session_key)
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

            # Validate domain filter
            safe_domain = validate_domain(domain or [])

            # Validate order clause
            if order:
                order = _validate_order(order)

            # Validate user-provided field names
            if fields:
                _validate_fields(fields)

            # Smart field selection if none specified
            if not fields:
                try:
                    all_fields = await gateway.field_inspector.get_fields(
                        client,
                        model,
                    )
                    fields = gateway.field_inspector.get_important_fields(
                        model,
                        all_fields,
                    )
                except Exception:
                    logger.debug("Field inspection failed for %s", model)
                    fields = None  # Let Odoo use default fields

            # Clamp limit and offset
            limit = max(1, min(limit, _MAX_LIMIT))
            offset = max(0, offset)

            kwargs: dict[str, Any] = {
                "fields": fields,
                "limit": limit,
                "offset": offset,
            }
            if order:
                kwargs["order"] = order

            records = await client.execute_kw(
                model,
                "search_read",
                [safe_domain],
                kwargs,
            )

            # Apply RBAC field filtering
            if isinstance(records, list):
                records = gateway.rbac.filter_response_fields(
                    records,
                    model,
                    user_groups,
                    is_admin,
                )

            return {
                "records": records,
                "count": len(records) if isinstance(records, list) else 0,
                "model": model,
            }

        except DomainValidationError as e:
            return {"error": f"Invalid domain filter: {e}"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in search_read")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def get_record(
        model: str,
        record_id: int,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Read a single record by ID from any Odoo model."""
        try:
            model = _validate_model(model)
            if record_id <= 0:
                return {"error": "record_id must be a positive integer"}
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "get_record", session_key)
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

            kwargs: dict[str, Any] = {}
            if fields:
                _validate_fields(fields)
                kwargs["fields"] = fields

            records = await client.execute_kw(
                model,
                "read",
                [[record_id]],
                kwargs,
            )

            if not records:
                return {"error": f"Record {record_id} not found in {model}"}

            record = records[0] if isinstance(records, list) else records

            # Apply RBAC field filtering
            if isinstance(record, dict):
                filtered = gateway.rbac.filter_response_fields(
                    [record],
                    model,
                    user_groups,
                    is_admin,
                )
                record = filtered[0]

            return {"record": record, "model": model}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_record")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def search_count(
        model: str,
        domain: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Count records matching a domain in an Odoo model."""
        try:
            model = _validate_model(model)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "search_count", session_key)
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

            # Validate domain filter
            safe_domain = validate_domain(domain or [])

            count = await client.execute_kw(
                model,
                "search_count",
                [safe_domain],
            )

            return {"count": count, "model": model}

        except DomainValidationError as e:
            return {"error": f"Invalid domain filter: {e}"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in search_count")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def create_record(
        model: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new record in an Odoo model."""
        try:
            model = _validate_model(model)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "create_record", session_key)
            if gate_error:
                return {"error": gate_error}

            # Check model restrictions for create
            restriction_msg = gateway.restrictions.check_model_access(
                model,
                "create",
                is_admin,
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Validate write values size/depth and field names
            _validate_write_values(values)
            for field_name in values:
                if not _WRITE_FIELD_RE.match(field_name):
                    return {"error": f"Invalid field name: {field_name!r}"}

            # Check blocked write fields
            for field_name in list(values.keys()):
                field_msg = gateway.restrictions.check_field_write(
                    model,
                    field_name,
                    is_admin,
                )
                if field_msg:
                    return {"error": field_msg}

            # Sanitize write values via RBAC
            values = gateway.rbac.sanitize_write_values(
                values,
                model,
                user_groups,
                is_admin,
            )

            record_id = await client.execute_kw(
                model,
                "create",
                [values],
            )

            return {"id": record_id, "model": model}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in create_record")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def update_record(
        model: str,
        record_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an existing record in an Odoo model."""
        try:
            model = _validate_model(model)
            if record_id <= 0:
                return {"error": "record_id must be a positive integer"}
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "update_record", session_key)
            if gate_error:
                return {"error": gate_error}

            # Check model restrictions for write
            restriction_msg = gateway.restrictions.check_model_access(
                model,
                "write",
                is_admin,
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Validate write values size/depth and field names
            _validate_write_values(values)
            for field_name in values:
                if not _WRITE_FIELD_RE.match(field_name):
                    return {"error": f"Invalid field name: {field_name!r}"}

            # Check blocked write fields
            for field_name in list(values.keys()):
                field_msg = gateway.restrictions.check_field_write(
                    model,
                    field_name,
                    is_admin,
                )
                if field_msg:
                    return {"error": field_msg}

            # Sanitize write values via RBAC
            values = gateway.rbac.sanitize_write_values(
                values,
                model,
                user_groups,
                is_admin,
            )

            await client.execute_kw(
                model,
                "write",
                [[record_id], values],
            )

            return {"success": True, "model": model, "id": record_id}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in update_record")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def delete_record(
        model: str,
        record_id: int,
    ) -> dict[str, Any]:
        """Delete a record from an Odoo model."""
        try:
            model = _validate_model(model)
            if record_id <= 0:
                return {"error": "record_id must be a positive integer"}
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "delete_record", session_key)
            if gate_error:
                return {"error": gate_error}

            # Check model restrictions for delete
            restriction_msg = gateway.restrictions.check_model_access(
                model,
                "delete",
                is_admin,
            )
            if restriction_msg:
                return {"error": restriction_msg}

            await client.execute_kw(
                model,
                "unlink",
                [[record_id]],
            )

            return {"success": True, "model": model, "id": record_id}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in delete_record")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def read_group(
        model: str,
        fields: list[str],
        groupby: list[str],
        domain: list[Any] | None = None,
        limit: int | None = None,
        orderby: str | None = None,
    ) -> dict[str, Any]:
        """Read grouped/aggregated data from an Odoo model."""
        try:
            model = _validate_model(model)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "read_group", session_key)
            if gate_error:
                return {"error": gate_error}

            # Validate field and groupby names
            _validate_agg_fields(fields)
            _validate_fields(groupby)

            # Check model restrictions
            restriction_msg = gateway.restrictions.check_model_access(
                model,
                "read",
                is_admin,
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Validate domain filter
            safe_domain = validate_domain(domain or [])

            # Validate orderby
            if orderby:
                orderby = _validate_order(orderby)

            kwargs: dict[str, Any] = {}
            if limit is not None:
                kwargs["limit"] = max(1, min(limit, 500))
            if orderby:
                kwargs["orderby"] = orderby

            result = await client.execute_kw(
                model,
                "read_group",
                [safe_domain, fields, groupby],
                kwargs,
            )

            # Apply RBAC field filtering to grouped results
            groups = result if isinstance(result, list) else []
            if groups:
                groups = gateway.rbac.filter_response_fields(
                    groups,
                    model,
                    user_groups,
                    is_admin,
                )

            return {
                "groups": groups,
                "model": model,
            }

        except DomainValidationError as e:
            return {"error": f"Invalid domain filter: {e}"}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in read_group")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def execute_method(
        model: str,
        method: str,
        record_ids: list[int] | None = None,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a method on an Odoo model."""
        try:
            model = _validate_model(model)
            method = _validate_method(method)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = next(iter(gateway.auth_managers.keys()), "default")
            gate_error = await security_gate(gateway, "execute_method", session_key)
            if gate_error:
                return {"error": gate_error}

            # Block direct ORM methods -- these are exposed through
            # dedicated CRUD tools that enforce field-level checks.
            if method in _BLOCKED_ORM_METHODS:
                return {
                    "error": (
                        f"Method '{method}' cannot be called via "
                        "execute_method. Use the dedicated CRUD "
                        "tools instead."
                    )
                }

            # Check model restrictions
            restriction_msg = gateway.restrictions.check_model_access(
                model,
                "write",
                is_admin,
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Check method restrictions
            method_msg = gateway.restrictions.check_method_access(
                model,
                method,
                is_admin,
            )
            if method_msg:
                return {"error": method_msg}

            # Validate args depth/size
            _validate_method_args(args or [], kwargs or {})

            # Validate and prepend record_ids if provided
            call_args: list[Any] = []
            if record_ids is not None:
                if not record_ids:
                    return {"error": "record_ids must not be empty"}
                if len(record_ids) > _MAX_LIMIT:
                    return {"error": f"Too many record_ids (max {_MAX_LIMIT})"}
                for rid in record_ids:
                    if isinstance(rid, bool) or not isinstance(rid, int) or rid <= 0:
                        return {
                            "error": "record_ids must contain only positive integers"
                        }
                call_args.append(record_ids)
            if args:
                call_args.extend(args)

            result = await client.execute_kw(
                model,
                method,
                call_args,
                kwargs or {},
            )

            return {"result": result, "model": model, "method": method}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in execute_method")
            return {"error": gateway.sanitize_error(e)}
