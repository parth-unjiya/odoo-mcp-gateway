"""Audit logger: records all security-relevant operations."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_crypt",
        "secret",
        "token",
        "api_key",
        "signup_token",
        "signup_expiration",
        "credential",
    }
)

_MAX_VALUE_LENGTH = 200

logger = logging.getLogger("odoo_mcp_gateway.audit")


@dataclass
class AuditEntry:
    """A single audit log entry capturing a gateway operation."""

    timestamp: str
    session_id: str
    user_id: int
    user_login: str
    tool: str
    model: str | None = None
    operation: str = ""
    args_summary: dict[str, Any] = field(default_factory=dict)
    result: str = "success"
    record_ids: list[int] = field(default_factory=list)
    duration_ms: float = 0.0
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict suitable for JSON serialization."""
        return asdict(self)


class AuditLogger:
    """Writes audit entries to a configured backend.

    Supported backends:
    - 'file': append JSON lines to a file via a dedicated logging.Logger
    - 'stdout': write JSON lines to stdout
    - 'logger': use Python logging framework
    """

    def __init__(
        self,
        backend: str = "file",
        log_path: str = "audit.log",
    ) -> None:
        self._backend = backend
        self._log_path = Path(log_path)
        self._file_logger: logging.Logger | None = None

        if self._backend == "file":
            self._file_logger = self._create_file_logger(self._log_path)

    @staticmethod
    def _create_file_logger(log_path: Path) -> logging.Logger:
        """Create a dedicated logger that writes JSON lines to a file."""
        # Use a unique logger name per path to avoid handler conflicts
        logger_name = f"odoo_mcp_gateway.audit.file.{log_path}"
        file_logger = logging.getLogger(logger_name)
        file_logger.setLevel(logging.INFO)
        file_logger.propagate = False

        # Only add handler if this logger doesn't already have one
        if not file_logger.handlers:
            handler = logging.FileHandler(str(log_path), encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            file_logger.addHandler(handler)

        return file_logger

    def log(self, entry: AuditEntry) -> None:
        """Write an audit entry to the configured backend."""
        data = entry.to_dict()
        line = json.dumps(data, default=str)

        if self._backend == "file":
            if self._file_logger is not None:
                self._file_logger.info(line)
        elif self._backend == "stdout":
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        elif self._backend == "logger":
            logger.info(line)
        else:
            raise ValueError(f"Unknown audit backend: '{self._backend}'")

    @staticmethod
    def create_entry(
        session_id: str,
        user_id: int,
        user_login: str,
        tool: str,
        model: str | None = None,
        operation: str = "",
        args: dict[str, Any] | None = None,
        result: str = "success",
        record_ids: list[int] | None = None,
        duration_ms: float = 0.0,
        error_message: str | None = None,
    ) -> AuditEntry:
        """Create an audit entry with sanitized args."""
        return AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            user_id=user_id,
            user_login=user_login,
            tool=tool,
            model=model,
            operation=operation,
            args_summary=AuditLogger._sanitize_args(args or {}),
            result=result,
            record_ids=record_ids or [],
            duration_ms=duration_ms,
            error_message=error_message,
        )

    @staticmethod
    def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
        """Sanitize arguments for logging: truncate long values, mask secrets.

        Recursively redacts sensitive keys at all nesting levels.
        Note: sensitive data in positional args (not dict keys) is not redacted.
        """
        return _redact_dict(args)


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive keys in a dict."""
    sanitized: dict[str, Any] = {}
    for key, value in d.items():
        lower_key = key.lower()
        if lower_key in _SENSITIVE_KEYS or "password" in lower_key:
            sanitized[key] = "***"
            continue

        if isinstance(value, dict):
            redacted = _redact_dict(value)
            str_val = json.dumps(redacted, default=str)
            if len(str_val) > _MAX_VALUE_LENGTH:
                sanitized[key] = str_val[:_MAX_VALUE_LENGTH] + "...[truncated]"
            else:
                sanitized[key] = redacted
        elif isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
            sanitized[key] = value[:_MAX_VALUE_LENGTH] + "...[truncated]"
        elif isinstance(value, list):
            redacted_list = [
                _redact_dict(item) if isinstance(item, dict) else item for item in value
            ]
            str_val = json.dumps(redacted_list, default=str)
            if len(str_val) > _MAX_VALUE_LENGTH:
                sanitized[key] = str_val[:_MAX_VALUE_LENGTH] + "...[truncated]"
            else:
                sanitized[key] = redacted_list
        else:
            sanitized[key] = value

    return sanitized
