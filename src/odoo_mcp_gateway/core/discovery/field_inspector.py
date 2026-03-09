"""Field inspector: retrieves and caches field metadata for Odoo models."""

from __future__ import annotations

import time
from typing import Any

from .models import FieldInfo

# Internal / framework fields to always exclude from the "important" list.
INTERNAL_FIELDS = frozenset(
    {
        "__last_update",
        "write_uid",
        "write_date",
        "create_uid",
        "create_date",
        "id",
        "display_name",
        "access_url",
        "access_token",
        "access_warning",
        "activity_ids",
        "activity_state",
        "activity_summary",
        "activity_type_id",
        "activity_user_id",
        "activity_date_deadline",
        "activity_exception_decoration",
        "activity_exception_icon",
        "message_ids",
        "message_follower_ids",
        "message_partner_ids",
        "message_channel_ids",
        "message_has_error",
        "message_has_sms_error",
        "message_needaction",
        "message_needaction_counter",
        "message_is_follower",
        "message_main_attachment_id",
        "message_attachment_count",
        "website_message_ids",
        "has_message",
    }
)

# Maximum number of "important" fields returned.
_MAX_IMPORTANT = 25

# Priority field names that, when present, are always included.
_PRIORITY_NAMES = ("name", "display_name", "state", "stage_id")

# Key relational field names.
_KEY_RELATIONS = ("partner_id", "user_id", "company_id", "currency_id")


class FieldInspector:
    """Retrieves field definitions with TTL-based caching."""

    def __init__(self, cache_ttl: int = 3600) -> None:
        # Cache maps model name -> (timestamp, field dict)
        self._cache: dict[str, tuple[float, dict[str, FieldInfo]]] = {}
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_fields(
        self,
        client: Any,
        model: str,
        force_refresh: bool = False,
    ) -> dict[str, FieldInfo]:
        """Return field definitions for *model*, using cache when valid."""
        if not force_refresh and self._is_cache_valid(model):
            return self._cache[model][1]

        raw: dict[str, dict[str, Any]] = await client.execute_kw(
            model,
            "fields_get",
            [],
            {
                "attributes": [
                    "type",
                    "string",
                    "required",
                    "readonly",
                    "store",
                    "relation",
                    "selection",
                    "help",
                ]
            },
        )

        fields: dict[str, FieldInfo] = {}
        for fname, fdata in raw.items():
            selection_raw = fdata.get("selection") or []
            selection = [(str(k), str(v)) for k, v in selection_raw]
            fields[fname] = FieldInfo(
                name=fname,
                field_type=fdata.get("type", ""),
                string=fdata.get("string", ""),
                required=bool(fdata.get("required", False)),
                readonly=bool(fdata.get("readonly", False)),
                store=bool(fdata.get("store", True)),
                relation=fdata.get("relation"),
                selection=selection,
                help_text=fdata.get("help") or "",
                is_binary=fdata.get("type") == "binary",
            )

        self._cache[model] = (time.monotonic(), fields)
        return fields

    def get_important_fields(
        self,
        model: str,
        fields: dict[str, FieldInfo],
    ) -> list[str]:
        """Return up to 25 "important" field names.

        Priority order:
        1. ``name`` / ``display_name``
        2. ``state`` / ``stage_id``
        3. Required fields
        4. Key relational fields (``partner_id``, ``user_id``, ...)
        5. Date / datetime fields
        6. Amount / monetary fields
        Excluded: binary, one2many, internal fields, non-stored computed.
        """
        candidates: list[str] = []
        remaining: list[str] = []

        for fname, finfo in fields.items():
            # Exclude always-skip categories.
            if fname in INTERNAL_FIELDS:
                continue
            if finfo.is_binary:
                continue
            if finfo.field_type == "one2many":
                continue
            if not finfo.store and finfo.readonly:
                continue

            if fname in _PRIORITY_NAMES:
                candidates.append(fname)
            elif finfo.required:
                remaining.append(fname)
            elif fname in _KEY_RELATIONS:
                remaining.append(fname)
            elif finfo.field_type in ("date", "datetime"):
                remaining.append(fname)
            elif finfo.field_type in ("monetary", "float", "integer"):
                remaining.append(fname)
            else:
                remaining.append(fname)

        result = candidates + remaining
        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for f in result:
            if f not in seen:
                seen.add(f)
                deduped.append(f)
        return deduped[:_MAX_IMPORTANT]

    def invalidate_cache(self, model: str | None = None) -> None:
        """Invalidate cached fields for *model*, or all models if ``None``."""
        if model is None:
            self._cache.clear()
        else:
            self._cache.pop(model, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_cache_valid(self, model: str) -> bool:
        """Return ``True`` if cached data for *model* has not expired."""
        entry = self._cache.get(model)
        if entry is None:
            return False
        ts, _ = entry
        return (time.monotonic() - ts) < self._cache_ttl
