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
