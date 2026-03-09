"""Data models for Odoo model/field/method discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AccessLevel(Enum):
    """Access level for a discovered Odoo model."""

    BLOCKED = "blocked"
    ADMIN_ONLY = "admin_only"
    READ_ONLY = "read_only"
    FULL_CRUD = "full_crud"


@dataclass
class ModelInfo:
    """Metadata about a discovered Odoo model."""

    name: str  # e.g. "sale.order"
    description: str  # e.g. "Sales Order"
    is_custom: bool  # True if non-stock module
    is_transient: bool  # True for wizard models
    module: str  # originating module
    state: str  # "base" or "manual"
    access_level: AccessLevel = AccessLevel.BLOCKED
    field_count: int = 0


@dataclass
class FieldInfo:
    """Metadata about a single field on an Odoo model."""

    name: str
    field_type: str  # "char", "many2one", etc.
    string: str  # Human-readable label
    required: bool = False
    readonly: bool = False
    store: bool = True
    relation: str | None = None
    selection: list[tuple[str, str]] = field(default_factory=list)
    help_text: str = ""
    is_binary: bool = False
