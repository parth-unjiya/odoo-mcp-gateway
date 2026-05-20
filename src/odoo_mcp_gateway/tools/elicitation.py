"""MCP elicitation helpers (ADR-008).

The MCP spec's ``elicitation/create`` lets a server pause mid-tool and
ask the client for additional input — useful for "you said
create_record but forgot the partner_id" scenarios where today we'd
just error out. Pairs naturally with our existing
``get_create_requirements`` infrastructure.

Two helpers:

* :func:`detect_missing_required_fields` — given a model + the
  caller-supplied values, return the list of required fields the
  caller forgot. Pure function; reads the cached field schema via
  ``gateway.field_inspector``.
* :func:`elicit_missing_fields` — given a FastMCP ``Context``, a
  list of missing field names, and a model, send an
  ``elicitation/create`` request and return the filled-in dict (or
  ``None`` if the client declined / doesn't support elicitation).

The integration pattern in a tool:

.. code-block:: python

    missing = await detect_missing_required_fields(gateway, client, model, values)
    if missing:
        filled = await elicit_missing_fields(ctx, model, missing, gateway)
        if filled is not None:
            values = {**values, **filled}
        else:
            return {
                "error": f"Required field(s) missing: {sorted(missing)}",
            }

If the client doesn't support elicitation (most don't yet), the
helper returns ``None`` and the tool falls back to its existing
error path. So we add a UX upgrade for capable clients without
breaking older ones.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

    from odoo_mcp_gateway.server import GatewayContext

logger = logging.getLogger(__name__)

# The spec caps elicitation schemas at primitive types only (string,
# integer, number, boolean). Anything more complex must be encoded
# as a string and parsed by the tool. We surface relations as
# strings so the client renders them as text input the user fills
# with an ID.
_PRIMITIVE_TYPE_FOR_ODOO: dict[str, str] = {
    "char": "string",
    "text": "string",
    "html": "string",
    "selection": "string",
    "many2one": "integer",
    "integer": "integer",
    "float": "number",
    "monetary": "number",
    "boolean": "boolean",
    "date": "string",
    "datetime": "string",
}


async def detect_missing_required_fields(
    gateway: GatewayContext,
    client: Any,
    model: str,
    values: dict[str, Any],
    field_info: dict[str, Any] | None = None,
) -> list[str]:
    """Return the names of required fields the caller didn't supply.

    Uses the cached field schema via ``field_inspector.get_fields``
    so this is a cheap O(fields) operation in steady state. Falls
    through to an empty list (treated as "nothing missing") if the
    schema can't be fetched — Odoo will surface the real error
    when the create runs.

    Excludes fields the caller HAS supplied even with ``""`` /
    ``None`` — the existing empty-required check in
    ``_validate_writable_fields`` handles those. Elicitation is for
    fields the caller didn't mention at all.

    *field_info* may be supplied by the caller to avoid a duplicate
    ``fields_get`` round-trip when the same schema is read again
    immediately afterward (e.g. by ``_validate_writable_fields``).
    """
    if field_info is None:
        try:
            field_info = await gateway.field_inspector.get_fields(client, model)
        except Exception:
            logger.debug("Field inspection failed for %s — skipping elicitation", model)
            return []

    if not field_info:
        return []

    supplied = set(values.keys())
    missing: list[str] = []
    for fname, info in field_info.items():
        if not getattr(info, "required", False):
            continue
        if fname in supplied:
            continue
        # Skip readonly / computed required fields — Odoo populates
        # them server-side, and the caller can't supply them anyway.
        if getattr(info, "readonly", False):
            continue
        missing.append(fname)
    return missing


def _build_elicit_schema(
    gateway: GatewayContext,
    model: str,
    field_names: list[str],
    field_info: dict[str, Any],
) -> dict[str, Any]:
    """Construct a JSON-schema-shaped ``requestedSchema`` payload.

    The MCP spec restricts elicitation schemas to flat primitives.
    Each field becomes a top-level property keyed by its Odoo name,
    with a ``description`` derived from the field's label/help.
    Selection fields gain an ``enum`` constraint listing the valid
    keys; many2one fields are typed as ``integer`` (the client
    enters the related record's ID).
    """
    properties: dict[str, Any] = {}
    for fname in field_names:
        info = field_info.get(fname)
        if info is None:
            properties[fname] = {"type": "string"}
            continue
        ftype = getattr(info, "field_type", "") or ""
        primitive = _PRIMITIVE_TYPE_FOR_ODOO.get(ftype, "string")
        prop: dict[str, Any] = {"type": primitive}
        label = getattr(info, "string", None) or fname
        help_text = getattr(info, "help_text", None) or ""
        description = label
        if help_text:
            description = f"{label} — {help_text}"
        prop["description"] = description
        # Selection fields: surface the allowed keys as an enum so
        # the client renders a dropdown instead of free text.
        if ftype == "selection":
            selection = getattr(info, "selection", None) or []
            if selection:
                prop["enum"] = [str(k) for k, _label in selection]
        # Relation hint for many2one — clients can show a helpful
        # placeholder via the description.
        if ftype == "many2one":
            relation = getattr(info, "relation", None)
            if relation:
                prop["description"] = f"{description} (ID from {relation})"
        properties[fname] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": list(field_names),
    }


async def elicit_missing_fields(
    ctx: Context[Any, Any, Any] | None,
    gateway: GatewayContext,
    client: Any,
    model: str,
    missing: list[str],
) -> dict[str, Any] | None:
    """Ask the client to fill in *missing* required fields.

    Returns the filled-in dict on accept, or ``None`` if:

    * The client doesn't expose elicitation (FastMCP returns
      AttributeError / no session is available).
    * The user declines or cancels.
    * Any error happens during the elicitation round-trip.

    The caller treats ``None`` as "fall through to the regular
    missing-field error" — we don't want to make tools harder to
    use just because the elicitation channel failed.
    """
    if ctx is None or not missing:
        return None

    try:
        field_info = await gateway.field_inspector.get_fields(client, model)
    except Exception:
        logger.debug("Could not load field schema for elicitation; aborting")
        return None

    requested_schema = _build_elicit_schema(gateway, model, missing, field_info)
    message = (
        f"Creating a record on '{model}' requires the following fields. "
        "Please provide them to continue."
    )

    try:
        session = ctx.request_context.session
        result = await session.elicit_form(
            message=message,
            requestedSchema=requested_schema,
            related_request_id=ctx.request_id,
        )
    except Exception:
        logger.debug(
            "Elicitation channel unavailable or errored — caller will "
            "fall back to standard missing-field error",
            exc_info=True,
        )
        return None

    # The spec defines ``action`` as one of "accept" / "decline" /
    # "cancel". Only "accept" yields usable data.
    action = getattr(result, "action", None)
    if action != "accept":
        logger.debug("Elicitation %s by client; missing fields stand", action)
        return None

    content = getattr(result, "content", None)
    if not isinstance(content, dict):
        return None

    # Filter to the fields we actually requested — defensive against
    # a client returning extra keys.
    filled: dict[str, Any] = {}
    for fname in missing:
        if fname in content:
            filled[fname] = content[fname]
    return filled or None
