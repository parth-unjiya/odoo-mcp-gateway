"""Tests covering PostgreSQL leak suppression in ErrorSanitizer.

Bad-type domains, broken queries, and connection failures can bubble
psycopg2 internals up through the JSON-RPC error body. These tests
verify the new regex patterns strip those leaks.

Maps to P2-9 from the Odoo 19 pass-2 audit.
"""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.security.sanitizer import ErrorSanitizer


@pytest.fixture()
def sanitizer() -> ErrorSanitizer:
    return ErrorSanitizer()


class TestPsycopg2LeakSuppression:
    def test_strips_psycopg2_class_name(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            "psycopg2.errors.InvalidTextRepresentation: invalid input syntax "
            'for type integer: "not_an_int"'
        )
        result = sanitizer.sanitize(msg)
        assert "psycopg2" not in result
        assert "errors.InvalidTextRepresentation" not in result

    def test_strips_psycopg2_operational_error(self, sanitizer: ErrorSanitizer) -> None:
        msg = "psycopg2.OperationalError: connection terminated"
        result = sanitizer.sanitize(msg)
        assert "psycopg2" not in result

    def test_strips_sql_line_indicator(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            'syntax error at or near ")"\n'
            "LINE 1: SELECT * FROM res_partner WHERE id = )"
        )
        result = sanitizer.sanitize(msg)
        assert "LINE 1:" not in result

    def test_strips_relation_does_not_exist(self, sanitizer: ErrorSanitizer) -> None:
        msg = 'relation "secret_table" does not exist'
        result = sanitizer.sanitize(msg)
        assert "secret_table" not in result
        assert "does not exist" not in result

    def test_strips_detail_hint_block(self, sanitizer: ErrorSanitizer) -> None:
        msg = (
            "INSERT failed\n"
            "DETAIL:  Key (id)=(42) already exists.\n"
            "HINT:  Use a different value.\n"
            'CONTEXT:  SQL statement "INSERT INTO res_partner ..."'
        )
        result = sanitizer.sanitize(msg)
        assert "Key (id)=(42)" not in result
        assert "CONTEXT:" not in result
        assert "DETAIL:" not in result

    def test_strips_combined_bad_domain_error(self, sanitizer: ErrorSanitizer) -> None:
        """End-to-end: the kind of error a bad-type domain leaks today."""
        msg = (
            "psycopg2.errors.InvalidTextRepresentation: invalid input "
            'syntax for type integer: "not_an_int"\n'
            'LINE 1: SELECT "res_partner"."id" FROM "res_partner" '
            "WHERE...\n"
            "DETAIL:  bad value"
        )
        result = sanitizer.sanitize(msg)
        assert "psycopg2" not in result
        assert "LINE 1:" not in result
        assert "DETAIL:" not in result
        assert "res_partner" not in result

    def test_contains_internals_detects_psycopg2(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        assert sanitizer._contains_internals("psycopg2.errors.SomeError") is True

    def test_contains_internals_detects_line(self, sanitizer: ErrorSanitizer) -> None:
        assert sanitizer._contains_internals("LINE 1: SELECT x") is True

    def test_contains_internals_detects_relation_msg(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        assert (
            sanitizer._contains_internals('relation "private" does not exist') is True
        )

    def test_known_odoo_error_with_psycopg2_remainder_strips(
        self, sanitizer: ErrorSanitizer
    ) -> None:
        """When a mapped Odoo error wraps a psycopg2 leak, the friendly
        prefix is preserved but the leak is suppressed.
        """
        msg = (
            'odoo.exceptions.ValidationError: psycopg2.errors.SyntaxError: near "FROM"'
        )
        result = sanitizer.sanitize(msg)
        assert "Validation error" in result
        assert "psycopg2" not in result
