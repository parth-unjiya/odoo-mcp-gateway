"""Bulk CRUD operations (ADR-010).

Two tools — :func:`bulk_create` and :func:`bulk_update` — that wrap
Odoo's native batch ``execute_kw(model, 'create', [records])`` and
``execute_kw(model, 'write', [ids, values])`` calls. Each Odoo call
is wrapped in a single PostgreSQL transaction by the Odoo HTTP
handler, so a single chunk is all-or-nothing — exactly what callers
asking for atomicity want.

Chunk semantics — important caveat:

* Each ``chunk_size``-sized batch is ONE Odoo call = ONE transaction
  = all-or-nothing.
* Chunks are NOT atomic with respect to each other. If chunk 2 fails,
  chunk 1 is already committed.
* The chunk_size default of 200 keeps individual request payloads
  under Odoo's default body-size limit while still amortising the
  HTTP overhead. Callers needing >200-record atomicity should raise
  ``chunk_size`` and let Odoo reject if its size cap is hit.

What this module DOES NOT ship:

* No ``transaction_begin`` / ``transaction_commit`` tools. Odoo's
  HTTP API does not expose cross-request transactions; the only path
  to implement them would be an Odoo-side companion module, which
  violates the gateway's "zero Odoo-side code" rule.
* No cross-model atomicity (e.g. ``create order → create lines →
  confirm`` in one shot). Use ``execute_method`` against an Odoo
  server-side action that bundles the workflow.

Every bulk operation runs through the same security pipeline as the
single-record tool: restrictions → field-write checks → RBAC
sanitization → version-aware field renames → writable-field
pre-flight. We DO NOT loosen any check for batch performance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context, FastMCP

from odoo_mcp_gateway.core.security import security_gate
from odoo_mcp_gateway.server import (
    _get_auth_manager,
    _get_client,
    get_current_session_key,
)
from odoo_mcp_gateway.tools.crud import (
    _MAX_LIMIT,
    _WRITE_FIELD_RE,
    _apply_value_renames,
    _reject_id_field,
    _validate_model,
    _validate_writable_fields,
    _validate_write_values,
)

if TYPE_CHECKING:
    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

# Default chunk size — balance between transaction atomicity (bigger
# is more atomic) and Odoo request-body limits (smaller is safer).
# 200 is Odoo's own default for view_grouped operations and works on
# every supported version.
_DEFAULT_CHUNK_SIZE = 200

# Hard upper bound on a single bulk operation. Even if the caller
# raises chunk_size, the TOTAL records count is capped here so an
# accidental ``range(1_000_000)`` can't OOM the gateway.
_MAX_TOTAL_RECORDS = 5_000


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split ``items`` into chunks of at most ``size`` each."""
    if size <= 0:
        size = _DEFAULT_CHUNK_SIZE
    return [items[i : i + size] for i in range(0, len(items), size)]


def register_bulk_tools(server: FastMCP, gateway: GatewayContext) -> None:
    """Register ``bulk_create`` and ``bulk_update`` on the MCP server."""

    @server.tool()
    async def bulk_create(
        model: str,
        records: list[dict[str, Any]],
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        dry_run: bool = False,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> dict[str, Any]:
        """Create many records of *model* in batches.

        Each chunk of up to ``chunk_size`` records is ONE Odoo
        ``execute_kw('create', [chunk])`` call = ONE database
        transaction. Within a chunk the create is all-or-nothing.
        Across chunks atomicity is NOT guaranteed — if chunk 2 fails,
        chunk 1 stays committed. To get >chunk_size-record atomicity,
        raise ``chunk_size`` and let Odoo reject if its body limit is
        hit.

        Args:
            model: Target Odoo model.
            records: List of ``{field: value}`` dicts. Each dict goes
                through the same restrictions / RBAC / field
                validation pipeline as ``create_record``.
            chunk_size: Maximum records per Odoo call. Default 200.
            dry_run: If True, validate every record without writing.

        Returns:
            ``{"created_ids": [...], "chunks": N, "total": N}`` on
            success, or ``{"error": str, "partial_ids": [...],
            "completed_chunks": N}`` if a chunk failed mid-flight.
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
            gate_error = await security_gate(gateway, "bulk_create", session_key)
            if gate_error:
                return {"error": gate_error}

            # Top-level shape checks.
            if not isinstance(records, list):
                return {"error": "records must be a list of dicts"}
            if not records:
                return {"error": "records must not be empty"}
            if len(records) > _MAX_TOTAL_RECORDS:
                return {
                    "error": (
                        f"Too many records ({len(records)}, max "
                        f"{_MAX_TOTAL_RECORDS}). Submit in multiple "
                        "bulk_create calls if needed."
                    )
                }
            for i, rec in enumerate(records):
                if not isinstance(rec, dict):
                    type_name = type(rec).__name__
                    return {"error": f"records[{i}] must be a dict, got {type_name}"}

            # Model restriction check (once, applies to every record).
            restriction_msg = gateway.restrictions.check_model_access(
                model, "create", is_admin
            )
            if restriction_msg:
                return {"error": restriction_msg}

            # Per-record validation: id rejection, field names, blocked
            # writes, RBAC sanitization, version renames. We apply
            # these BEFORE any Odoo call so dry_run reports the same
            # errors as a real call.
            validated: list[dict[str, Any]] = []
            for i, rec in enumerate(records):
                id_msg = _reject_id_field(rec)
                if id_msg:
                    return {"error": f"records[{i}]: {id_msg}"}
                _validate_write_values(rec)
                for field_name in rec:
                    if not _WRITE_FIELD_RE.match(field_name):
                        return {
                            "error": f"records[{i}]: invalid field name {field_name!r}"
                        }
                for field_name in list(rec.keys()):
                    field_msg = gateway.restrictions.check_field_write(
                        model, field_name, is_admin
                    )
                    if field_msg:
                        return {"error": f"records[{i}]: {field_msg}"}

                rec = gateway.rbac.sanitize_write_values(
                    rec, model, user_groups, is_admin
                )
                rec = _apply_value_renames(gateway, model, rec)
                writable_msg = await _validate_writable_fields(
                    gateway,
                    client,
                    model,
                    rec,
                    check_required_non_empty=True,
                )
                if writable_msg:
                    return {"error": f"records[{i}]: {writable_msg}"}
                validated.append(rec)

            chunks = _chunked(validated, chunk_size)

            if dry_run:
                return {
                    "dry_run": True,
                    "model": model,
                    "action": "bulk_create",
                    "total": len(validated),
                    "chunks": len(chunks),
                    "chunk_size": chunk_size,
                    "first_record_preview": validated[0] if validated else None,
                }

            # Execute. Each chunk = one Odoo transaction; if a chunk
            # fails we surface what we DID create so the caller can
            # decide whether to retry just the remainder or roll back
            # via their own logic (Odoo has no client-visible
            # cross-request rollback).
            created_ids: list[int] = []
            total_records = len(validated)
            for idx, chunk in enumerate(chunks):
                try:
                    result = await client.execute_kw(
                        model,
                        "create",
                        [chunk],
                    )
                except Exception as exc:
                    return {
                        "error": (
                            f"Chunk {idx + 1}/{len(chunks)} failed: "
                            f"{gateway.sanitize_error(exc)}"
                        ),
                        "partial_ids": created_ids,
                        "completed_chunks": idx,
                        "total_chunks": len(chunks),
                    }
                # Odoo returns either an int (single record) or a list.
                if isinstance(result, list):
                    created_ids.extend(int(r) for r in result)
                elif isinstance(result, int):
                    created_ids.append(result)

                # Progress notification — if the client passed a
                # progressToken in the request meta, FastMCP surfaces
                # ``ctx.report_progress`` as a live channel; otherwise
                # it's a silent no-op so older clients aren't affected.
                if ctx is not None:
                    try:
                        await ctx.report_progress(
                            progress=len(created_ids),
                            total=total_records,
                            message=(
                                f"Created {len(created_ids)} of "
                                f"{total_records} records "
                                f"(chunk {idx + 1}/{len(chunks)})"
                            ),
                        )
                    except Exception:
                        # Progress is best-effort — a broken channel
                        # never blocks the bulk operation.
                        logger.debug(
                            "Progress notification failed",
                            exc_info=True,
                        )

            return {
                "created_ids": created_ids,
                "model": model,
                "chunks": len(chunks),
                "total": len(created_ids),
            }

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in bulk_create")
            return {"error": gateway.sanitize_error(e)}

    @server.tool()
    async def bulk_update(
        model: str,
        record_ids: list[int],
        values: dict[str, Any],
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        dry_run: bool = False,
        ctx: Context[Any, Any, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply *values* to every record in *record_ids*.

        Wraps ``execute_kw(model, 'write', [ids, values])``. Each chunk
        of up to ``chunk_size`` IDs is ONE Odoo call. The write itself
        is atomic per chunk; across chunks it is NOT atomic.

        The ``values`` dict is validated ONCE (it applies to every
        record), so this is much cheaper than calling ``update_record``
        in a loop.

        Args:
            model: Target Odoo model.
            record_ids: IDs to update. Deduped automatically.
            values: ``{field: value}`` applied to every record.
            chunk_size: Maximum IDs per Odoo call. Default 200.
            dry_run: If True, validate without writing.

        Returns:
            ``{"updated_count": N, "chunks": N}`` on success, or
            partial-state diagnostic on chunk failure.
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
            gate_error = await security_gate(gateway, "bulk_update", session_key)
            if gate_error:
                return {"error": gate_error}

            # record_ids validation: list of positive ints, deduped,
            # under the hard cap.
            if not isinstance(record_ids, list):
                return {"error": "record_ids must be a list of integers"}
            if not record_ids:
                return {"error": "record_ids must not be empty"}
            seen: set[int] = set()
            deduped: list[int] = []
            for i, rid in enumerate(record_ids):
                if isinstance(rid, bool) or not isinstance(rid, int) or rid <= 0:
                    return {
                        "error": (
                            f"record_ids[{i}] must be a positive integer; got {rid!r}"
                        )
                    }
                if rid not in seen:
                    seen.add(rid)
                    deduped.append(rid)
            if len(deduped) > _MAX_TOTAL_RECORDS:
                return {
                    "error": (
                        f"Too many record_ids ({len(deduped)}, max "
                        f"{_MAX_TOTAL_RECORDS})"
                    )
                }
            if len(deduped) > _MAX_LIMIT and len(deduped) <= _MAX_TOTAL_RECORDS:
                # Per-record-id volume sanity: the existing single-call
                # _MAX_LIMIT is for one execute_kw; bulk_update can
                # exceed it because we chunk, but we still warn so
                # operators know this is a heavy operation.
                logger.info(
                    "bulk_update on %d records (above per-call limit %d) — "
                    "splitting across %d chunks",
                    len(deduped),
                    _MAX_LIMIT,
                    (len(deduped) + chunk_size - 1) // max(chunk_size, 1),
                )

            # values validation (same pipeline as update_record).
            if not isinstance(values, dict):
                return {"error": "values must be a dict"}
            if not values:
                return {"error": "values must not be empty"}

            id_msg = _reject_id_field(values)
            if id_msg:
                return {"error": id_msg}
            _validate_write_values(values)
            for field_name in values:
                if not _WRITE_FIELD_RE.match(field_name):
                    return {"error": f"Invalid field name: {field_name!r}"}

            restriction_msg = gateway.restrictions.check_model_access(
                model, "write", is_admin
            )
            if restriction_msg:
                return {"error": restriction_msg}

            for field_name in list(values.keys()):
                field_msg = gateway.restrictions.check_field_write(
                    model, field_name, is_admin
                )
                if field_msg:
                    return {"error": field_msg}

            values = gateway.rbac.sanitize_write_values(
                values, model, user_groups, is_admin
            )
            values = _apply_value_renames(gateway, model, values)
            writable_msg = await _validate_writable_fields(
                gateway,
                client,
                model,
                values,
                check_required_non_empty=False,
            )
            if writable_msg:
                return {"error": writable_msg}

            id_chunks = _chunked(deduped, chunk_size)

            if dry_run:
                return {
                    "dry_run": True,
                    "model": model,
                    "action": "bulk_update",
                    "total_ids": len(deduped),
                    "chunks": len(id_chunks),
                    "chunk_size": chunk_size,
                    "validated_values": values,
                }

            completed = 0
            total_records = len(deduped)
            for idx, chunk in enumerate(id_chunks):
                try:
                    await client.execute_kw(
                        model,
                        "write",
                        [chunk, values],
                    )
                    completed += len(chunk)
                except Exception as exc:
                    return {
                        "error": (
                            f"Chunk {idx + 1}/{len(id_chunks)} failed: "
                            f"{gateway.sanitize_error(exc)}"
                        ),
                        "updated_count": completed,
                        "completed_chunks": idx,
                        "total_chunks": len(id_chunks),
                    }

                # See bulk_create for the progress-channel rationale.
                if ctx is not None:
                    try:
                        await ctx.report_progress(
                            progress=completed,
                            total=total_records,
                            message=(
                                f"Updated {completed} of "
                                f"{total_records} records "
                                f"(chunk {idx + 1}/{len(id_chunks)})"
                            ),
                        )
                    except Exception:
                        logger.debug(
                            "Progress notification failed",
                            exc_info=True,
                        )

            return {
                "updated_count": completed,
                "model": model,
                "chunks": len(id_chunks),
            }

        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in bulk_update")
            return {"error": gateway.sanitize_error(e)}

    # Register operation types for security middleware + rate-limit
    # classification. Both are write operations.
    from odoo_mcp_gateway.core.security import register_tool_operations

    register_tool_operations(
        {
            "bulk_create": "create",
            "bulk_update": "write",
        }
    )
