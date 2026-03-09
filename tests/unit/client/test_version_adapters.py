"""Tests for version adapters."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.client.exceptions import OdooVersionError
from odoo_mcp_gateway.core.version.adapters import (
    V17Adapter,
    V18Adapter,
    V19Adapter,
    VersionAdapter,
    get_adapter,
)
from odoo_mcp_gateway.core.version.detector import VersionInfo


def _version(major: int, edition: str = "community") -> VersionInfo:
    return VersionInfo(
        major=major,
        minor=0,
        micro=0,
        edition=edition,
        full_string=f"{major}.0",
    )


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


class TestGetAdapter:
    def test_returns_v17(self) -> None:
        adapter = get_adapter(_version(17))
        assert isinstance(adapter, V17Adapter)

    def test_returns_v18(self) -> None:
        adapter = get_adapter(_version(18))
        assert isinstance(adapter, V18Adapter)

    def test_returns_v19(self) -> None:
        adapter = get_adapter(_version(19))
        assert isinstance(adapter, V19Adapter)

    def test_unsupported_raises(self) -> None:
        with pytest.raises(OdooVersionError, match="No adapter"):
            get_adapter(_version(16))

    def test_all_return_version_adapter(self) -> None:
        for major in (17, 18, 19):
            adapter = get_adapter(_version(major))
            assert isinstance(adapter, VersionAdapter)


# ------------------------------------------------------------------
# V17
# ------------------------------------------------------------------


class TestV17Adapter:
    def setup_method(self) -> None:
        self.adapter = V17Adapter()

    def test_session_info_fields(self) -> None:
        fields = self.adapter.get_session_info_fields()
        assert "uid" in fields
        assert "username" in fields
        assert "user_context" in fields
        assert "is_admin" in fields
        assert "db" in fields

    def test_no_bearer_token(self) -> None:
        assert self.adapter.supports_bearer_token() is False

    def test_normalize_domain_returns_list(self) -> None:
        domain = [("name", "=", "test")]
        result = self.adapter.normalize_domain(domain)
        assert result == [("name", "=", "test")]
        assert result is not domain  # must be a copy

    def test_normalize_context_returns_dict(self) -> None:
        ctx = {"lang": "en_US"}
        result = self.adapter.normalize_context(ctx)
        assert result == {"lang": "en_US"}
        assert result is not ctx  # must be a copy


# ------------------------------------------------------------------
# V18
# ------------------------------------------------------------------


class TestV18Adapter:
    def setup_method(self) -> None:
        self.adapter = V18Adapter()

    def test_session_info_fields_includes_server_version(self) -> None:
        fields = self.adapter.get_session_info_fields()
        assert "server_version" in fields

    def test_no_bearer_token(self) -> None:
        assert self.adapter.supports_bearer_token() is False

    def test_normalize_domain(self) -> None:
        domain = [("active", "=", True)]
        assert self.adapter.normalize_domain(domain) == [("active", "=", True)]

    def test_normalize_context(self) -> None:
        ctx = {"tz": "UTC"}
        assert self.adapter.normalize_context(ctx) == {"tz": "UTC"}


# ------------------------------------------------------------------
# V19
# ------------------------------------------------------------------


class TestV19Adapter:
    def setup_method(self) -> None:
        self.adapter = V19Adapter()

    def test_supports_bearer_token(self) -> None:
        assert self.adapter.supports_bearer_token() is True

    def test_session_info_fields_includes_support_url(self) -> None:
        fields = self.adapter.get_session_info_fields()
        assert "support_url" in fields

    def test_normalize_domain(self) -> None:
        domain = ["|", ("a", "=", 1), ("b", "=", 2)]
        result = self.adapter.normalize_domain(domain)
        assert result == ["|", ("a", "=", 1), ("b", "=", 2)]

    def test_normalize_context_copy(self) -> None:
        ctx = {"lang": "en_US", "tz": "Europe/Brussels"}
        result = self.adapter.normalize_context(ctx)
        assert result == ctx
        assert result is not ctx


# ------------------------------------------------------------------
# Domain normalization edge cases
# ------------------------------------------------------------------


class TestDomainNormalization:
    @pytest.mark.parametrize("major", [17, 18, 19])
    def test_empty_domain(self, major: int) -> None:
        adapter = get_adapter(_version(major))
        assert adapter.normalize_domain([]) == []

    @pytest.mark.parametrize("major", [17, 18, 19])
    def test_complex_domain(self, major: int) -> None:
        domain = ["&", ("state", "=", "sale"), ("amount", ">", 100)]
        adapter = get_adapter(_version(major))
        result = adapter.normalize_domain(domain)
        assert len(result) == 3


# ------------------------------------------------------------------
# Context normalization edge cases
# ------------------------------------------------------------------


class TestContextNormalization:
    @pytest.mark.parametrize("major", [17, 18, 19])
    def test_empty_context(self, major: int) -> None:
        adapter = get_adapter(_version(major))
        assert adapter.normalize_context({}) == {}

    @pytest.mark.parametrize("major", [17, 18, 19])
    def test_preserves_all_keys(self, major: int) -> None:
        ctx = {"lang": "en_US", "tz": "UTC", "uid": 1}
        adapter = get_adapter(_version(major))
        result = adapter.normalize_context(ctx)
        assert result == ctx
