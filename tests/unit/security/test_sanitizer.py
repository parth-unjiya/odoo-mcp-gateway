"""Tests for the error sanitizer."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer


@pytest.fixture()
def sanitizer() -> ErrorSanitizer:
    return ErrorSanitizer()


# ── File path stripping ────────────────────────────────────────────


class TestStripPaths:
    def test_strip_python_path(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Error in /home/user/odoo/addons/sale/models/sale.py:42"
        result = sanitizer.sanitize(msg)
        assert "/home/user" not in result
        assert ".py" not in result

    def test_strip_multiple_paths(self, sanitizer: ErrorSanitizer) -> None:
        msg = "File /a/b.py, line 10, in /c/d.py"
        result = sanitizer.sanitize(msg)
        assert "/a/b.py" not in result
        assert "/c/d.py" not in result


# ── SQL stripping ──────────────────────────────────────────────────


class TestStripSQL:
    def test_strip_select(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Query failed: SELECT * FROM res_partner WHERE id = 1"
        result = sanitizer.sanitize(msg)
        assert "SELECT" not in result

    def test_strip_insert(self, sanitizer: ErrorSanitizer) -> None:
        msg = "INSERT INTO res_partner (name) VALUES ('test') failed"
        result = sanitizer.sanitize(msg)
        assert "INSERT" not in result

    def test_strip_update(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Error: UPDATE res_partner SET name='x' WHERE id=1"
        result = sanitizer.sanitize(msg)
        assert "UPDATE" not in result

    def test_strip_delete(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Failed: DELETE FROM res_partner WHERE id = 1"
        result = sanitizer.sanitize(msg)
        assert "DELETE FROM" not in result

    def test_strip_drop(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Error: DROP TABLE res_partner"
        result = sanitizer.sanitize(msg)
        assert "DROP TABLE" not in result


# ── Traceback stripping ────────────────────────────────────────────


class TestStripTracebacks:
    def test_strip_traceback(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            "Traceback (most recent call last):\n"
            '  File "/odoo/models.py", line 42\n'
            "    raise ValueError()\n"
            "ValueError: bad value"
        )
        result = sanitizer.sanitize(msg)
        assert "Traceback" not in result
        assert "most recent call" not in result

    def test_strip_nested_traceback(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            "Something went wrong\n"
            "Traceback (most recent call last):\n"
            '  File "test.py", line 1\n'
            "Error happened"
        )
        result = sanitizer.sanitize(msg)
        assert "Traceback" not in result


# ── Database name stripping ────────────────────────────────────────


class TestStripDatabaseNames:
    def test_strip_database_reference(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Connection to database 'my_production_db' failed"
        result = sanitizer.sanitize(msg)
        assert "my_production_db" not in result

    def test_strip_db_equals(self, sanitizer: ErrorSanitizer) -> None:
        msg = "db=my_db connection timeout"
        result = sanitizer.sanitize(msg)
        assert "my_db" not in result

    def test_strip_database_colon(self, sanitizer: ErrorSanitizer) -> None:
        msg = 'database: "prod_odoo" unreachable'
        result = sanitizer.sanitize(msg)
        assert "prod_odoo" not in result


# ── Preserve user-friendly parts ───────────────────────────────────


class TestPreserveUserFriendly:
    def test_preserve_simple_message(self, sanitizer: ErrorSanitizer) -> None:
        msg = "Record not found"
        result = sanitizer.sanitize(msg)
        assert result == "Record not found"

    def test_empty_message_returns_default(self, sanitizer: ErrorSanitizer) -> None:
        result = sanitizer.sanitize("")
        assert "unexpected error" in result.lower()

    def test_none_like_empty(self, sanitizer: ErrorSanitizer) -> None:
        result = sanitizer.sanitize("")
        assert result != ""


# ── Error map ──────────────────────────────────────────────────────


class TestErrorMap:
    def test_access_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.AccessError: not allowed to do that"
        result = sanitizer.sanitize(msg)
        assert "Access denied" in result

    def test_access_denied_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.AccessDenied: wrong password"
        result = sanitizer.sanitize(msg)
        assert "Authentication failed" in result

    def test_validation_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.ValidationError: field is required"
        result = sanitizer.sanitize(msg)
        assert "Validation error" in result

    def test_user_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.UserError: cannot confirm"
        result = sanitizer.sanitize(msg)
        assert "Operation failed" in result

    def test_missing_error_mapped(self, sanitizer: ErrorSanitizer) -> None:
        msg = "odoo.exceptions.MissingError: record deleted"
        result = sanitizer.sanitize(msg)
        assert "Record not found" in result

    def test_error_map_with_internals_in_remainder(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        msg = "odoo.exceptions.AccessError: SELECT * FROM res_partner WHERE id = 1"
        result = sanitizer.sanitize(msg)
        assert "SELECT" not in result
        assert "Access denied" in result


# ── sanitize_exception ─────────────────────────────────────────────


class TestSanitizeException:
    def test_generic_exception(self, sanitizer: ErrorSanitizer) -> None:
        exc = RuntimeError("Something failed at /home/user/test.py:42")
        result = sanitizer.sanitize_exception(exc)
        assert "/home/user" not in result

    def test_value_error(self, sanitizer: ErrorSanitizer) -> None:
        exc = ValueError("Invalid input")
        result = sanitizer.sanitize_exception(exc)
        assert "Invalid input" in result
