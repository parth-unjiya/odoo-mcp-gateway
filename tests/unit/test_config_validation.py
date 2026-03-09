"""Tests for Settings validators in config.py.

Validates URL scheme/host requirements (SSRF prevention) and
database name sanitization (SQL injection prevention).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from odoo_mcp_gateway.config import Settings


class TestUrlValidation:
    """Verify odoo_url only accepts http/https with a valid host."""

    def test_valid_http_url(self) -> None:
        s = Settings(odoo_url="http://localhost:8069")
        assert s.odoo_url == "http://localhost:8069"

    def test_valid_https_url(self) -> None:
        s = Settings(odoo_url="https://odoo.example.com")
        assert s.odoo_url == "https://odoo.example.com"

    def test_valid_url_with_port(self) -> None:
        s = Settings(odoo_url="https://odoo.example.com:443")
        assert s.odoo_url == "https://odoo.example.com:443"

    def test_valid_url_with_path(self) -> None:
        s = Settings(odoo_url="http://proxy.local:8080/odoo")
        assert s.odoo_url == "http://proxy.local:8080/odoo"

    def test_trailing_slash_stripped(self) -> None:
        s = Settings(odoo_url="http://localhost:8069/")
        assert s.odoo_url == "http://localhost:8069"

    def test_rejects_ftp_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http or https"):
            Settings(odoo_url="ftp://evil.com")

    def test_rejects_javascript_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http or https"):
            Settings(odoo_url="javascript://alert(1)")

    def test_rejects_file_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http or https"):
            Settings(odoo_url="file:///etc/passwd")

    def test_rejects_data_scheme(self) -> None:
        with pytest.raises(ValidationError, match="http or https"):
            Settings(odoo_url="data://text/html,<h1>evil</h1>")

    def test_rejects_empty_host(self) -> None:
        with pytest.raises(ValidationError):
            Settings(odoo_url="http://")

    def test_rejects_no_scheme(self) -> None:
        with pytest.raises(ValidationError, match="scheme"):
            Settings(odoo_url="localhost:8069")

    def test_rejects_double_slash_only(self) -> None:
        with pytest.raises(ValidationError):
            Settings(odoo_url="//localhost:8069")


class TestDbValidation:
    """Verify odoo_db only accepts safe alphanumeric names."""

    def test_valid_db_name_simple(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", odoo_db="mydb")
        assert s.odoo_db == "mydb"

    def test_valid_db_name_with_underscore(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", odoo_db="my_db_v2")
        assert s.odoo_db == "my_db_v2"

    def test_valid_db_name_with_dot(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", odoo_db="company.prod")
        assert s.odoo_db == "company.prod"

    def test_valid_db_name_with_hyphen(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", odoo_db="my-db")
        assert s.odoo_db == "my-db"

    def test_empty_db_allowed(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", odoo_db="")
        assert s.odoo_db == ""

    def test_rejects_sql_injection_semicolon(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db="test; DROP TABLE")

    def test_rejects_single_quote(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db="db'OR'1=1")

    def test_rejects_double_quote(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db='db"test')

    def test_rejects_space(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db="my db")

    def test_rejects_backtick(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db="db`cmd`")

    def test_rejects_slash(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db="../../etc")

    def test_rejects_equals(self) -> None:
        with pytest.raises(ValidationError, match="alphanumeric"):
            Settings(odoo_url="http://localhost:8069", odoo_db="x=1")


class TestTransportValidation:
    """Verify mcp_transport only accepts allowed literals."""

    def test_valid_stdio(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", mcp_transport="stdio")
        assert s.mcp_transport == "stdio"

    def test_valid_streamable_http(self) -> None:
        s = Settings(
            odoo_url="http://localhost:8069",
            mcp_transport="streamable-http",
        )
        assert s.mcp_transport == "streamable-http"

    def test_rejects_invalid_transport(self) -> None:
        with pytest.raises(ValidationError):
            Settings(odoo_url="http://localhost:8069", mcp_transport="grpc")


class TestLogLevelValidation:
    """Verify mcp_log_level literal constraints."""

    def test_valid_debug(self) -> None:
        s = Settings(odoo_url="http://localhost:8069", mcp_log_level="DEBUG")
        assert s.mcp_log_level == "DEBUG"

    def test_rejects_invalid_level(self) -> None:
        with pytest.raises(ValidationError):
            Settings(odoo_url="http://localhost:8069", mcp_log_level="TRACE")
