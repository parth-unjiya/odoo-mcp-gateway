"""Tests for the audit logger."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from odoo_mcp_gateway.core.security.audit import AuditEntry, AuditLogger


@pytest.fixture()
def sample_entry() -> AuditEntry:
    return AuditEntry(
        timestamp="2025-01-01T00:00:00+00:00",
        session_id="sess-123",
        user_id=1,
        user_login="admin",
        tool="search_read",
        model="res.partner",
        operation="read",
        args_summary={"domain": "[]", "fields": "['name']"},
        result="success",
        record_ids=[1, 2, 3],
        duration_ms=42.5,
    )


# ── File backend ───────────────────────────────────────────────────


class TestFileBackend:
    def test_writes_json_line(self, tmp_path: Path, sample_entry: AuditEntry) -> None:
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(backend="file", log_path=str(log_file))
        logger.log(sample_entry)

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["tool"] == "search_read"
        assert data["user_login"] == "admin"

    def test_appends_multiple_entries(
        self, tmp_path: Path, sample_entry: AuditEntry
    ) -> None:
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(backend="file", log_path=str(log_file))
        logger.log(sample_entry)
        logger.log(sample_entry)

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_valid_json_format(self, tmp_path: Path, sample_entry: AuditEntry) -> None:
        log_file = tmp_path / "audit.log"
        logger = AuditLogger(backend="file", log_path=str(log_file))
        logger.log(sample_entry)

        data = json.loads(log_file.read_text().strip())
        assert "timestamp" in data
        assert "session_id" in data
        assert "tool" in data
        assert "result" in data


# ── Stdout backend ─────────────────────────────────────────────────


class TestStdoutBackend:
    def test_writes_to_stdout(
        self, capsys: pytest.CaptureFixture[str], sample_entry: AuditEntry
    ) -> None:
        logger = AuditLogger(backend="stdout")
        logger.log(sample_entry)

        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["tool"] == "search_read"


# ── Logger backend ─────────────────────────────────────────────────


class TestLoggerBackend:
    def test_uses_python_logging(
        self, caplog: pytest.LogCaptureFixture, sample_entry: AuditEntry
    ) -> None:
        logger = AuditLogger(backend="logger")
        with caplog.at_level(logging.INFO, logger="odoo_mcp_gateway.audit"):
            logger.log(sample_entry)

        assert len(caplog.records) == 1
        data = json.loads(caplog.records[0].message)
        assert data["tool"] == "search_read"


# ── Invalid backend ────────────────────────────────────────────────


class TestInvalidBackend:
    def test_unknown_backend_raises(self, sample_entry: AuditEntry) -> None:
        logger = AuditLogger(backend="invalid_backend")
        with pytest.raises(ValueError, match="Unknown audit backend"):
            logger.log(sample_entry)


# ── Sanitize args ──────────────────────────────────────────────────


class TestSanitizeArgs:
    def test_password_never_logged(self) -> None:
        args = {"username": "admin", "password": "secret123"}
        result = AuditLogger._sanitize_args(args)
        assert result["password"] == "***"
        assert result["username"] == "admin"

    def test_api_key_masked(self) -> None:
        args = {"api_key": "sk-abc123"}
        result = AuditLogger._sanitize_args(args)
        assert result["api_key"] == "***"

    def test_long_string_truncated(self) -> None:
        args = {"data": "x" * 500}
        result = AuditLogger._sanitize_args(args)
        assert result["data"].endswith("...[truncated]")
        assert len(result["data"]) < 500

    def test_normal_args_preserved(self) -> None:
        args = {"model": "res.partner", "limit": 10}
        result = AuditLogger._sanitize_args(args)
        assert result == args

    def test_password_variants_masked(self) -> None:
        args = {"password_crypt": "hash", "my_password_field": "secret"}
        result = AuditLogger._sanitize_args(args)
        assert result["password_crypt"] == "***"
        assert result["my_password_field"] == "***"


# ── create_entry ───────────────────────────────────────────────────


class TestCreateEntry:
    def test_creates_with_timestamp(self) -> None:
        entry = AuditLogger.create_entry(
            session_id="sess-1",
            user_id=1,
            user_login="admin",
            tool="search_read",
        )
        assert entry.timestamp is not None
        assert "T" in entry.timestamp

    def test_args_are_sanitized(self) -> None:
        entry = AuditLogger.create_entry(
            session_id="sess-1",
            user_id=1,
            user_login="admin",
            tool="test",
            args={"password": "secret", "name": "test"},
        )
        assert entry.args_summary["password"] == "***"
        assert entry.args_summary["name"] == "test"

    def test_duration_tracking(self) -> None:
        entry = AuditLogger.create_entry(
            session_id="sess-1",
            user_id=1,
            user_login="admin",
            tool="test",
            duration_ms=123.45,
        )
        assert entry.duration_ms == 123.45
