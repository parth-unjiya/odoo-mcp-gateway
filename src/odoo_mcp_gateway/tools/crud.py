"""Generic CRUD tools that work with any Odoo model."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context, FastMCP

from odoo_mcp_gateway.core.security import (
    DANGEROUS_CONTEXT_KEYS,
    filter_dangerous_context_keys,
    security_gate,
)
from odoo_mcp_gateway.server import (
    _get_auth_manager,
    _get_client,
    get_current_session_key,
)
from odoo_mcp_gateway.utils.domain_builder import (
    DomainValidationError,
    validate_domain,
)

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)


def _apply_field_renames(
    gateway: GatewayContext,
    model: str,
    fields: list[str] | None,
) -> list[str] | None:
    """Translate user-supplied field names to version-appropriate aliases.

    If the active version adapter knows that, e.g., ``tax_id`` was renamed
    to ``tax_ids`` on ``sale.order.line`` in Odoo 19, this rewrites the
    request before Odoo sees it — sparing the AI client a raw KeyError.

    Returns the (possibly rewritten) list, or the original input when no
    adapter is loaded or no renames apply.
    """
    if not fields:
        return fields
    adapter = getattr(gateway, "version_adapter", None)
    if adapter is None:
        return fields
    try:
        renames = adapter.get_renamed_fields(model)
    except Exception:
        # An adapter bug must never block CRUD; just log and pass through.
        logger.debug("Adapter.get_renamed_fields failed for %s", model, exc_info=True)
        return fields
    if not renames:
        return fields
    return [renames.get(f, f) for f in fields]


def _apply_field_renames_with_suffix(
    gateway: GatewayContext,
    model: str,
    fields: list[str] | None,
) -> list[str] | None:
    """Apply renames to fields that may carry an Odoo suffix (``:sum``, ``:month``).

    Used by ``read_group`` where the input is e.g. ``["amount_total:sum"]``
    or ``["create_date:month"]``.  The suffix is preserved verbatim.
    """
    if not fields:
        return fields
    adapter = getattr(gateway, "version_adapter", None)
    if adapter is None:
        return fields
    try:
        renames = adapter.get_renamed_fields(model)
    except Exception:
        logger.debug("Adapter.get_renamed_fields failed for %s", model, exc_info=True)
        return fields
    if not renames:
        return fields
    out: list[str] = []
    for f in fields:
        # Split once on ':' — dotted paths (partner_id.name) don't use ':'
        base, sep, suffix = f.partition(":")
        new_base = renames.get(base, base)
        out.append(f"{new_base}{sep}{suffix}" if sep else new_base)
    return out


def _apply_value_renames(
    gateway: GatewayContext,
    model: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Translate user-supplied field names in a write/create values dict.

    Mirrors :func:`_apply_field_renames` but for the keys of the
    ``values`` mapping passed to ``create``/``write``.
    """
    if not values:
        return values
    adapter = getattr(gateway, "version_adapter", None)
    if adapter is None:
        return values
    try:
        renames = adapter.get_renamed_fields(model)
    except Exception:
        logger.debug("Adapter.get_renamed_fields failed for %s", model, exc_info=True)
        return values
    if not renames:
        return values
    # If the caller supplied BOTH old and new names, the explicit new name
    # wins (don't clobber it by overwriting with the rewritten old value).
    rewritten: dict[str, Any] = {}
    for key, val in values.items():
        new_key = renames.get(key, key)
        if new_key in rewritten and new_key != key:
            continue
        rewritten[new_key] = val
    return rewritten


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
    # Security: lowercase to prevent case-based bypass of blocked method checks
    # (e.g., "Sudo" would bypass "sudo" in _ALWAYS_BLOCKED_METHODS)
    method = method.strip().lower()
    if not method or len(method) > _MAX_METHOD_LEN or not _METHOD_RE.match(method):
        raise ValueError(f"Invalid method name: {method!r}")
    return method


# Aggregate field suffix MUST be a closed allowlist matching Odoo's
# read_group spec. Previously this matched any ``[a-z]+`` which accepted
# garbage like ``amount_total:rmrf`` and only failed at Odoo's SQL
# planner with a leakable error message.
_AGG_FIELD_RE = re.compile(
    r"^[a-z][a-z0-9_]*"
    r"(?::(?:sum|avg|min|max|count|count_distinct|array_agg|bool_and|bool_or))?$"
)
_GROUPBY_FIELD_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*(?::(day|week|month|quarter|year))?$"
)


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


def _validate_groupby_fields(fields: list[str]) -> list[str]:
    """Validate groupby field names with optional temporal operators.

    Accepts plain fields (``state``), dotted fields (``partner_id.name``),
    and Odoo temporal grouping syntax (``create_date:month``).
    Valid temporal operators: day, week, month, quarter, year.
    """
    if not fields:
        return fields
    for f in fields:
        if not _GROUPBY_FIELD_RE.match(f):
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


def _reject_id_field(values: dict[str, Any]) -> str | None:
    """Reject explicit ``id`` in a write/create payload.

    Odoo silently ignores ``id`` on create and write, which means a caller
    asking to "update id" gets ``success: True`` for a no-op. Surface this
    as a clear error so the caller knows primary keys are immutable.
    Returns an error string when rejected, ``None`` otherwise.
    """
    if "id" in values:
        return (
            "Cannot set or modify 'id' field — primary keys are immutable "
            "and assigned by Odoo. Remove 'id' from values."
        )
    return None


async def _validate_writable_fields(
    gateway: GatewayContext,
    client: Any,
    model: str,
    values: dict[str, Any],
    check_required_non_empty: bool = False,
    field_info: dict[str, Any] | None = None,
) -> str | None:
    """Pre-flight check: reject writes to readonly/computed fields.

    Odoo silently ignores writes to readonly/computed fields, which gives
    callers a false ``success: True`` response. Use the field inspector to
    catch this before the call.

    Two related guards are applied:

    * Required-field empty-write rejection is ALWAYS on. Writing
      ``""`` to a required field is a silent-no-op on Odoo's side and
      surfaces as false-success — we reject regardless of whether the
      call is create or update.
    * ``check_required_non_empty`` (the caller flag) extends the check
      to *missing required fields* on create. For update we don't
      enforce this because partial updates legitimately omit fields.

    *field_info* may be supplied by the caller to share a previously-
    fetched schema (e.g. by ``create_record`` which also calls the
    elicitation detector) and avoid a duplicate ``fields_get`` round-
    trip per write.

    Returns an error string on rejection, or ``None`` to proceed. Falls
    through silently when field inspection itself fails (defense-in-depth:
    Odoo will still apply its own ACLs).
    """
    if field_info is None:
        try:
            field_info = await gateway.field_inspector.get_fields(client, model)
        except Exception:
            logger.debug("Field inspection failed for %s; skipping pre-flight", model)
            return None

    if not field_info:
        return None

    readonly_fields = [f for f in values if f in field_info and field_info[f].readonly]
    if readonly_fields:
        return (
            f"Cannot write to readonly/computed fields: {sorted(readonly_fields)}. "
            "These are computed by Odoo and cannot be set directly."
        )

    # Always: reject empty/whitespace-only strings for required fields.
    # This applies to BOTH create and update — Odoo silently ignores
    # such writes on update, leaving the field unchanged and returning
    # success, which is the canonical silent-success failure mode.
    empty_required: list[str] = []
    for fname, val in values.items():
        info = field_info.get(fname)
        if info is None or not info.required:
            continue
        if isinstance(val, str) and not val.strip():
            empty_required.append(fname)
    if empty_required:
        return (
            f"Required field(s) {sorted(empty_required)} cannot be "
            "empty or whitespace-only — Odoo would silently ignore the "
            "write."
        )

    # Type-level validation for known field types. We don't recurse into
    # nested writes (many2one tuples, one2many commands); those are
    # Odoo's responsibility. We only catch the common "obvious wrong type"
    # cases that Odoo silently rejects or coerces.
    type_errors: list[str] = []
    for fname, val in values.items():
        info = field_info.get(fname)
        if info is None:
            continue
        # Skip None / explicit-clear values
        if val is None or val is False:
            continue

        ftype = getattr(info, "field_type", "") or ""

        # Date / Datetime: must look like a valid Odoo date string.
        if ftype in ("date", "datetime") and isinstance(val, str):
            if not _is_plausible_odoo_date(val, datetime_ok=ftype == "datetime"):
                expected = (
                    "date (YYYY-MM-DD)"
                    if ftype == "date"
                    else "datetime (YYYY-MM-DD HH:MM:SS)"
                )
                type_errors.append(f"{fname}={val!r}: not a valid {expected}")

        # Selection: value must be one of the allowed selection keys.
        elif ftype == "selection":
            selection = getattr(info, "selection", None) or []
            if selection:
                allowed_keys = {str(k) for k, _label in selection}
                if str(val) not in allowed_keys:
                    type_errors.append(
                        f"{fname}={val!r}: not in allowed values {sorted(allowed_keys)}"
                    )

        # Integer field with a string value Odoo would coerce or reject.
        elif ftype == "integer" and isinstance(val, str):
            try:
                int(val)
            except ValueError:
                type_errors.append(f"{fname}={val!r}: not a valid integer")

        # Float / Monetary: same pattern.
        elif ftype in ("float", "monetary") and isinstance(val, str):
            try:
                float(val)
            except ValueError:
                type_errors.append(f"{fname}={val!r}: not a valid number")

        # Boolean: accept True/False, the strings "true"/"false"/"1"/"0",
        # and 0/1. Anything else is suspicious.
        elif ftype == "boolean" and not isinstance(val, bool):
            if not (
                val in (0, 1)
                or (isinstance(val, str) and val.lower() in ("true", "false", "1", "0"))
            ):
                type_errors.append(f"{fname}={val!r}: not a valid boolean")

    if type_errors:
        return "Invalid value(s) for field type: " + "; ".join(type_errors)

    return None


_DATE_VALIDATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_VALIDATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _is_plausible_odoo_date(s: str, datetime_ok: bool = False) -> bool:
    """Return True if ``s`` looks like a valid Odoo date / datetime string.

    Performs both regex shape check AND a calendar-validity check via
    ``datetime.strptime`` (so ``"2026-13-99"`` is rejected even though it
    matches the regex).
    """
    from datetime import datetime as _dt

    # Datetime forms allowed only when datetime_ok is True
    if datetime_ok and _DATETIME_VALIDATE_RE.match(s):
        # Strip optional fractional seconds for strptime
        core = s.split(".", 1)[0].replace("T", " ")
        try:
            _dt.strptime(core, "%Y-%m-%d %H:%M:%S")
            return True
        except ValueError:
            return False

    if _DATE_VALIDATE_RE.match(s):
        try:
            _dt.strptime(s, "%Y-%m-%d")
            return True
        except ValueError:
            return False
    return False


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


# Re-export of the canonical context-key filter from core.security.
# Kept as a module-level alias so existing tests importing the private
# name continue to work — new code SHOULD import the public symbol
# from core.security directly.
_DANGEROUS_CONTEXT_KEYS = DANGEROUS_CONTEXT_KEYS

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
    """Validate and reconstruct a clean ORDER BY clause.

    Accepted forms (comma-separated):
        * ``field``
        * ``field asc`` / ``field desc``
        * ``a.b.c desc``  (up to MAX_FIELD_TRAVERSAL dotted segments)

    Rejects the common typo ``field,desc`` (comma instead of space)
    that the previous validator silently accepted as two ascending
    fields. The shared traversal-depth constant from ``domain_builder``
    is used so order-clauses and domain-leaves stay in sync.
    """
    # Late import to avoid a top-level cycle (crud imports utils).
    from odoo_mcp_gateway.utils.domain_builder import MAX_FIELD_TRAVERSAL

    if not order or not order.strip():
        return ""
    clean_parts = []
    directions = {"asc", "desc"}
    for part in order.split(","):
        tokens = part.strip().split()
        if not tokens:
            continue
        if len(tokens) > 2:
            raise ValueError(f"Invalid order clause: {part.strip()!r}")
        # If this part has no direction AND looks like 'asc'/'desc' on
        # its own, the caller almost certainly meant to attach it to
        # the previous part with a space, not a comma. Catch this
        # specific typo with a clear message instead of silently
        # accepting 'desc' as a (non-existent) field name.
        if len(tokens) == 1 and tokens[0].lower() in directions:
            raise ValueError(
                f"Invalid order clause: {part.strip()!r} — direction "
                "must follow a field, separated by a space (e.g. "
                "'create_date desc'), not by a comma."
            )
        field = tokens[0]
        if field.count(".") >= MAX_FIELD_TRAVERSAL:
            raise ValueError(
                f"Field traversal too deep in order: {field!r} "
                f"(max {MAX_FIELD_TRAVERSAL} segments)"
            )
        if not _FIELD_RE.match(field):
            raise ValueError(f"Invalid field in order: {field!r}")
        direction = "asc"
        if len(tokens) == 2:
            direction = tokens[1].lower()
            if direction not in directions:
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

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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

            # Reject non-positive limits before clamping. Otherwise
            # max(1, min(0, _MAX_LIMIT)) silently returns 1, which lets
            # ``limit=0`` (often "no results, please") sneak through and
            # return a single row.
            if limit <= 0:
                return {"error": "limit must be a positive integer (>=1)"}

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
                    # Fail safe: use a minimal field set rather than letting Odoo decide
                    fields = ["id", "display_name"]

            # Clamp limit and offset (limit is already > 0 here)
            limit = min(limit, _MAX_LIMIT)
            offset = max(0, offset)

            # Apply version-specific field renames (e.g. v19 tax_id -> tax_ids)
            fields = _apply_field_renames(gateway, model, fields)

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

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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
                # Apply version-specific field renames
                fields = _apply_field_renames(gateway, model, fields)
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

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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
        dry_run: bool = False,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new record in an Odoo model.

        Args:
            model: Target Odoo model name
            values: Field values for the new record
            dry_run: If True, validate without creating. Returns what would be sent.
            ctx: MCP Context auto-injected by FastMCP. Used for
                elicitation (ADR-008) when required fields are
                missing — the server asks the client to supply them
                rather than erroring out. Optional; if the client
                doesn't expose elicitation we fall back to the
                standard missing-field error.
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

            # Reject explicit 'id' before any other validation — Odoo
            # silently ignores it on create, hiding the caller's mistake.
            id_msg = _reject_id_field(values)
            if id_msg:
                return {"error": id_msg}

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

            # Apply version-specific field renames on the values payload
            values = _apply_value_renames(gateway, model, values)

            # ADR-008: Before failing on missing required fields, try
            # to elicit them from the client. Capable clients render
            # the elicitation as a form; older clients return a
            # decline and we fall through to the standard error path.
            # Skipped in dry_run (the caller is intentionally
            # previewing what THEY supplied, not what's missing).
            #
            # We fetch the field schema ONCE here and reuse it for
            # both elicitation detection and the writable-field
            # pre-flight below — avoiding a duplicate fields_get
            # round-trip per create.
            preloaded_field_info: dict[str, Any] | None = None
            if not dry_run:
                from odoo_mcp_gateway.tools.elicitation import (
                    detect_missing_required_fields,
                    elicit_missing_fields,
                )

                try:
                    preloaded_field_info = await gateway.field_inspector.get_fields(
                        client, model
                    )
                except Exception:
                    # Field inspection failure → skip BOTH elicitation
                    # and the writable-field pre-flight; Odoo will
                    # surface real errors at create time. We use {}
                    # (empty dict) rather than None so the downstream
                    # ``_validate_writable_fields(field_info={})``
                    # short-circuits without retrying the inspector.
                    preloaded_field_info = {}

                if preloaded_field_info:
                    missing = await detect_missing_required_fields(
                        gateway,
                        client,
                        model,
                        values,
                        field_info=preloaded_field_info,
                    )
                    if missing:
                        filled = await elicit_missing_fields(
                            ctx, gateway, client, model, missing
                        )
                        if filled:
                            # Merge elicited values WITHOUT overriding
                            # the caller's original keys — explicit
                            # intent always wins.
                            values = {**filled, **values}

            # Pre-flight: catch readonly/computed fields and empty required
            # values before Odoo silently swallows them. Runs for BOTH real
            # and dry_run modes — dry_run must surface the same errors as a
            # real call to be a useful preview. ``field_info`` is reused
            # from the elicitation-detection step above so we make at most
            # one ``fields_get`` per create.
            writable_msg = await _validate_writable_fields(
                gateway,
                client,
                model,
                values,
                check_required_non_empty=True,
                field_info=preloaded_field_info,
            )
            if writable_msg:
                return {"error": writable_msg}

            if dry_run:
                return {
                    "dry_run": True,
                    "model": model,
                    "action": "create",
                    "validated_values": values,
                    "field_count": len(values),
                }

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
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Update an existing record in an Odoo model.

        Args:
            model: Target Odoo model name
            record_id: ID of the record to update
            values: Field values to update
            dry_run: If True, validate without updating. Returns what would be sent.
        """
        try:
            model = _validate_model(model)
            if record_id <= 0:
                return {"error": "record_id must be a positive integer"}
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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

            # Reject explicit 'id' — primary keys are immutable. Without
            # this guard, Odoo silently ignores it and returns success.
            id_msg = _reject_id_field(values)
            if id_msg:
                return {"error": id_msg}

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

            # Apply version-specific field renames on the values payload
            values = _apply_value_renames(gateway, model, values)

            # Pre-flight: catch readonly/computed fields before Odoo
            # silently swallows the write. Runs for BOTH real and dry_run
            # modes so dry_run is a faithful preview. Empty-required
            # checks are skipped on update — partial updates with "" may
            # be valid (e.g. clearing an optional field).
            writable_msg = await _validate_writable_fields(
                gateway,
                client,
                model,
                values,
                check_required_non_empty=False,
            )
            if writable_msg:
                return {"error": writable_msg}

            if dry_run:
                return {
                    "dry_run": True,
                    "model": model,
                    "action": "update",
                    "id": record_id,
                    "validated_values": values,
                    "field_count": len(values),
                }

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
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete a record from an Odoo model.

        Args:
            model: Target Odoo model name
            record_id: ID of the record to delete
            dry_run: If True, validate without deleting. Returns what would happen.
        """
        try:
            model = _validate_model(model)
            if record_id <= 0:
                return {"error": "record_id must be a positive integer"}
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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

            if dry_run:
                return {
                    "dry_run": True,
                    "model": model,
                    "action": "delete",
                    "id": record_id,
                }

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

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
            gate_error = await security_gate(gateway, "read_group", session_key)
            if gate_error:
                return {"error": gate_error}

            # Validate field and groupby names
            _validate_agg_fields(fields)
            _validate_groupby_fields(groupby)

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

            # Reject non-positive explicit limits. ``limit=None`` keeps the
            # default cap of 500 (no caller-supplied limit at all).
            if limit is not None and limit <= 0:
                return {"error": "limit must be a positive integer (>=1)"}

            # Apply version-specific field renames (preserving Odoo suffixes
            # like ``:sum`` on aggregate fields and ``:month`` on groupby).
            renamed = _apply_field_renames_with_suffix(gateway, model, fields)
            fields = renamed if renamed is not None else fields
            renamed_groupby = _apply_field_renames_with_suffix(gateway, model, groupby)
            groupby = renamed_groupby if renamed_groupby is not None else groupby

            kwargs: dict[str, Any] = {}
            kwargs["limit"] = min(limit if limit is not None else 500, 500)
            if orderby:
                kwargs["orderby"] = orderby

            # `read_group` is deprecated in Odoo 17+ in favor of
            # `formatted_read_group` / `web_read_group`. Today it still
            # works, but it will eventually disappear. If the server
            # rejects the call as "not found", fall back to the newer
            # method so this tool keeps working across Odoo versions.
            try:
                result = await client.execute_kw(
                    model,
                    "read_group",
                    [safe_domain, fields, groupby],
                    kwargs,
                )
            except Exception as exc:
                msg = str(exc).lower()
                if (
                    "not exist" in msg
                    or "no attribute" in msg
                    or "not found" in msg
                    or "is not a valid method" in msg
                ):
                    try:
                        result = await client.execute_kw(
                            model,
                            "formatted_read_group",
                            [safe_domain, fields, groupby],
                            kwargs,
                        )
                    except Exception:
                        # Re-raise the original error if neither works
                        raise exc from None
                else:
                    raise

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
    async def get_defaults(
        model: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get default values for fields on an Odoo model.

        Returns default values that Odoo would pre-fill when
        creating a new record. Useful for understanding what
        values are auto-populated before calling create_record.
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
            gate_error = await security_gate(gateway, "get_defaults", session_key)
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

            # Validate fields if provided
            if fields:
                _validate_fields(fields)
            else:
                # Get all writable field names from field inspector
                try:
                    all_fields = await gateway.field_inspector.get_fields(
                        client,
                        model,
                    )
                    fields = [
                        fname
                        for fname, finfo in all_fields.items()
                        if not finfo.readonly
                    ]
                except Exception:
                    logger.debug("Field inspection failed for %s", model)
                    fields = []

            result = await client.execute_kw(
                model,
                "default_get",
                [fields],
            )

            # Apply RBAC field filtering
            if isinstance(result, dict):
                filtered = gateway.rbac.filter_response_fields(
                    [result],
                    model,
                    user_groups,
                    is_admin,
                )
                result = filtered[0]

            return {"defaults": result, "model": model}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_defaults")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def get_onchange(
        model: str,
        values: dict[str, Any],
        changed_field: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Simulate an onchange event on an Odoo model.

        Returns the values that would change when a field is
        modified in the UI. Useful for previewing side effects
        before calling create_record or update_record.

        Args:
            model: The Odoo model name
            values: Current field values (partial record)
            changed_field: The field that was just changed
            fields: Fields to check for onchange effects.
                Defaults to keys of values.
        """
        try:
            model = _validate_model(model)

            # Validate changed_field
            if not _WRITE_FIELD_RE.match(changed_field):
                return {"error": f"Invalid field name: {changed_field!r}"}

            # Validate values keys and size/depth
            _validate_write_values(values)
            for field_name in values:
                if not _WRITE_FIELD_RE.match(field_name):
                    return {"error": f"Invalid field name: {field_name!r}"}

            # Validate fields parameter — must be plain field names
            if fields:
                for f in fields:
                    if not _WRITE_FIELD_RE.match(f):
                        return {"error": f"Invalid field name in fields: {f!r}"}

            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False
            user_groups = auth_result.groups if auth_result else []

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
            gate_error = await security_gate(gateway, "get_onchange", session_key)
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

            # Field-write restriction parity with create_record /
            # update_record. Without this, an attacker could probe
            # whether a sensitive field (password, api_key) triggers
            # an onchange and infer field existence on the model — a
            # low-severity but real recon vector. We don't actually
            # WRITE these fields here (onchange is read-only) but
            # advertising that the gateway would honour them is the
            # same disclosure surface.
            for vk in list(values.keys()):
                fw_msg = gateway.restrictions.check_field_write(model, vk, is_admin)
                if fw_msg:
                    return {"error": fw_msg}

            # Version-adapter field rename parity. ``create_record`` and
            # ``update_record`` apply ``_apply_value_renames`` so callers
            # using v18 field names (e.g. ``tax_id``) hit the right v19
            # field (``tax_ids``). ``get_onchange`` previously skipped
            # this and silently forwarded the deprecated name. Apply the
            # rename to both ``values`` and ``changed_field`` so the
            # whole call shape is version-coherent.
            values = _apply_value_renames(gateway, model, values)
            renamed_single = _apply_field_renames(gateway, model, [changed_field])
            # _apply_field_renames returns None only for falsy input; we
            # always pass [changed_field] which is non-empty.
            changed_field = (renamed_single or [changed_field])[0]
            if fields:
                fields = _apply_field_renames(gateway, model, fields) or fields

            # Build the onchange spec
            field_onchange = {f: "" for f in (fields or list(values.keys()))}
            # Make sure changed_field is in the spec
            if changed_field not in field_onchange:
                field_onchange[changed_field] = ""

            result = await client.execute_kw(
                model,
                "onchange",
                [[], values, [changed_field], field_onchange],
            )

            changes = result.get("value", {}) if isinstance(result, dict) else {}
            warnings = result.get("warning") if isinstance(result, dict) else None

            # Detect Odoo 18+ deprecated-onchange behavior:
            # If we got an empty dict result OR an empty value with no warning,
            # the model may have migrated to web_save/computed fields and the
            # onchange API no longer triggers. Surface a hint to the caller.
            is_empty_result = (isinstance(result, dict) and not result) or (
                not changes and not warnings
            )
            api_status = "no_changes_or_unsupported" if is_empty_result else "ok"

            # Apply RBAC field filtering on onchange results
            if isinstance(changes, dict) and changes:
                filtered = gateway.rbac.filter_response_fields(
                    [changes],
                    model,
                    user_groups,
                    is_admin,
                )
                changes = filtered[0]

            response: dict[str, Any] = {
                "changes": changes,
                "warnings": warnings,
                "model": model,
                "_api_status": api_status,
            }
            if is_empty_result:
                response["_api_hint"] = (
                    "No onchange triggered for this model. This may be "
                    "normal, or the model may have moved to computed "
                    "fields on Odoo 18+. Verify by reading the record "
                    "after writing."
                )
            return response

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in get_onchange")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def execute_method(
        model: str,
        method: str,
        record_ids: list[int] | None = None,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute a method on an Odoo model.

        Args:
            model: Target Odoo model name
            method: Method name to execute
            record_ids: Optional list of record IDs to operate on
            args: Additional positional arguments for the method
            kwargs: Additional keyword arguments for the method
            dry_run: If True, validate without executing. Returns what would be called.
        """
        try:
            model = _validate_model(model)
            method = _validate_method(method)
            client = _get_client(gateway)
            auth_mgr = _get_auth_manager(gateway)
            auth_result = auth_mgr.auth_result
            is_admin = auth_result.is_admin if auth_result else False

            session_key = get_current_session_key() or next(
                iter(gateway.auth_managers.keys()), "default"
            )
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

            # Sanitize dangerous context keys from kwargs to prevent
            # bypassing archived-record filtering, audit trails,
            # mail tracking, or cross-company access boundaries.
            # Uses the shared helper from core.security so every tool
            # accepting a ``context`` kwarg filters the same way.
            stripped_context_keys: list[str] = []
            sanitized_kwargs: dict[str, Any] = dict(kwargs or {})
            if "context" in sanitized_kwargs:
                safe_ctx, stripped_context_keys = filter_dangerous_context_keys(
                    sanitized_kwargs["context"]
                )
                sanitized_kwargs["context"] = safe_ctx

            # Validate and prepend record_ids if provided
            call_args: list[Any] = []
            if record_ids is not None:
                if not record_ids:
                    return {"error": "record_ids must not be empty"}
                if len(record_ids) > _MAX_LIMIT:
                    return {"error": f"Too many record_ids (max {_MAX_LIMIT})"}
                # Reject duplicate record_ids to prevent workload
                # amplification (caller submits [1]*500 and we'd dispatch
                # the work 500x against a single record). Order is
                # preserved deterministically for callers that depend on
                # it via dict insertion semantics.
                unique_ids: list[int] = []
                seen: set[int] = set()
                for rid in record_ids:
                    if isinstance(rid, bool) or not isinstance(rid, int) or rid <= 0:
                        return {
                            "error": "record_ids must contain only positive integers"
                        }
                    if rid not in seen:
                        seen.add(rid)
                        unique_ids.append(rid)
                call_args.append(unique_ids)
            if args:
                call_args.extend(args)

            if dry_run:
                # Echo the sanitised kwargs (post-context-filter) so the
                # caller can see exactly what would have been dispatched.
                # ``stripped_context_keys`` is a hint that their context
                # contained bypass primitives the gateway refused to
                # forward — silently dropping these in real mode would
                # otherwise be invisible to the caller.
                dry_run_response: dict[str, Any] = {
                    "dry_run": True,
                    "model": model,
                    "method": method,
                    "record_ids": call_args[0] if record_ids is not None else None,
                    "args_count": len(call_args),
                    "sanitized_kwargs": sanitized_kwargs,
                }
                if stripped_context_keys:
                    dry_run_response["stripped_context_keys"] = stripped_context_keys
                return dry_run_response

            result = await client.execute_kw(
                model,
                method,
                call_args,
                sanitized_kwargs,
            )

            return {"result": result, "model": model, "method": method}

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in execute_method")
            return {"error": gateway.sanitize_error(e)}

    # Register operation types for security middleware
    from odoo_mcp_gateway.core.security import register_tool_operations

    register_tool_operations(
        {
            "search_read": "read",
            "get_record": "read",
            "search_count": "read",
            "create_record": "create",
            "update_record": "write",
            "delete_record": "delete",
            "read_group": "read",
            "get_defaults": "read",
            "get_onchange": "read",
            "execute_method": "write",
        }
    )
