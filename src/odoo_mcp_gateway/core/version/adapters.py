"""Version-specific adapters that normalise Odoo API differences."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from odoo_mcp_gateway.client.exceptions import OdooVersionError
from odoo_mcp_gateway.core.version.detector import VersionInfo


class VersionAdapter(ABC):
    """Abstract interface for version-specific behaviour."""

    @abstractmethod
    def get_session_info_fields(self) -> list[str]: ...

    @abstractmethod
    def normalize_domain(self, domain: list[Any]) -> list[Any]: ...

    @abstractmethod
    def normalize_context(self, context: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def supports_bearer_token(self) -> bool: ...

    # NOTE: The following methods (get_removed_fields, get_renamed_fields,
    # get_state_field_overrides) are infrastructure for future field-tracking
    # features.  They are tested but not yet wired into production code paths.
    # See V2_ROADMAP.md for integration plans.

    def get_removed_fields(self, model: str) -> frozenset[str]:
        """Return fields removed in this Odoo version for *model*.

        Subclasses override to encode known removals.  Returns an empty
        frozenset by default.
        """
        return frozenset()

    def get_renamed_fields(self, model: str) -> dict[str, str]:
        """Return fields renamed in this Odoo version for *model*.

        Returns a mapping of ``{old_name: new_name}``.
        Subclasses override to encode known renames.
        """
        return {}

    def get_state_field_overrides(self, model: str) -> dict[str, str] | None:
        """Return version-specific state field metadata for *model*.

        Returns ``None`` if no overrides exist.  Subclasses may return a
        dict with keys such as ``"field_name"`` and ``"selection_values"``.
        """
        return None


class V17Adapter(VersionAdapter):
    """Adapter for Odoo 17."""

    def get_session_info_fields(self) -> list[str]:
        return [
            "uid",
            "username",
            "user_context",
            "is_admin",
            "db",
        ]

    def normalize_domain(self, domain: list[Any]) -> list[Any]:
        return list(domain)

    def normalize_context(self, context: dict[str, Any]) -> dict[str, Any]:
        return dict(context)

    def supports_bearer_token(self) -> bool:
        return False


class V18Adapter(VersionAdapter):
    """Adapter for Odoo 18."""

    def get_session_info_fields(self) -> list[str]:
        return [
            "uid",
            "username",
            "user_context",
            "is_admin",
            "db",
            "server_version",
        ]

    def normalize_domain(self, domain: list[Any]) -> list[Any]:
        return list(domain)

    def normalize_context(self, context: dict[str, Any]) -> dict[str, Any]:
        return dict(context)

    def supports_bearer_token(self) -> bool:
        return False


# Known field renames in Odoo 19
_V19_FIELD_RENAMES: dict[str, dict[str, str]] = {
    "sale.order.line": {
        "product_uom": "product_uom_id",
        "tax_id": "tax_ids",
    },
}

# Known field removals in Odoo 19
_V19_FIELD_REMOVALS: dict[str, frozenset[str]] = {
    "crm.lead": frozenset({"mobile"}),
}


class V19Adapter(VersionAdapter):
    """Adapter for Odoo 19."""

    def get_session_info_fields(self) -> list[str]:
        return [
            "uid",
            "username",
            "user_context",
            "is_admin",
            "db",
            "server_version",
            "support_url",
        ]

    def normalize_domain(self, domain: list[Any]) -> list[Any]:
        return list(domain)

    def normalize_context(self, context: dict[str, Any]) -> dict[str, Any]:
        # Odoo 19 may introduce new default context keys; copy to avoid
        # mutation and strip unknown keys if needed.
        return dict(context)

    def supports_bearer_token(self) -> bool:
        return True

    def get_removed_fields(self, model: str) -> frozenset[str]:
        return _V19_FIELD_REMOVALS.get(model, frozenset())

    def get_renamed_fields(self, model: str) -> dict[str, str]:
        return dict(_V19_FIELD_RENAMES.get(model, {}))


_ADAPTER_MAP: dict[int, type[VersionAdapter]] = {
    17: V17Adapter,
    18: V18Adapter,
    19: V19Adapter,
}


def get_adapter(version: VersionInfo) -> VersionAdapter:
    """Return the appropriate :class:`VersionAdapter` for *version*.

    Raises :class:`OdooVersionError` if the major version has no adapter.
    """
    cls = _ADAPTER_MAP.get(version.major)
    if cls is None:
        raise OdooVersionError(f"No adapter available for Odoo {version.major}")
    return cls()
