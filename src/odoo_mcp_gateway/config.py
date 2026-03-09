"""Configuration via Pydantic Settings with validation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DB_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]*$")
_ALLOWED_SCHEMES = {"http", "https"}


class Settings(BaseSettings):
    """Gateway configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Odoo connection ──────────────────────────────────────────────
    odoo_url: str = "http://localhost:8069"
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_api_key: SecretStr = SecretStr("")

    # ── MCP transport ────────────────────────────────────────────────
    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── YAML config directory ────────────────────────────────────────
    config_dir: str = "."

    # ── Performance / limits ─────────────────────────────────────────
    cache_ttl_seconds: int = 3600
    max_concurrent_sessions: int = 100
    session_timeout_seconds: int = 1800

    # ── Rate limiting ────────────────────────────────────────────────
    rate_limit_global: int = 60  # requests per minute
    rate_limit_write: int = 20  # write operations per minute

    # ── Validators ───────────────────────────────────────────────────

    @field_validator("odoo_url")
    @classmethod
    def validate_odoo_url(cls, v: str) -> str:
        """Ensure the Odoo URL uses http or https only (SSRF prevention)."""
        if not v:
            return v

        # Parse scheme manually to avoid urllib edge-cases
        if "://" not in v:
            raise ValueError("odoo_url must include a scheme (http:// or https://)")

        scheme = v.split("://", maxsplit=1)[0].lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"odoo_url scheme must be http or https, got '{scheme}'")

        host_part = v.split("://", maxsplit=1)[1].split("/")[0].split(":")[0]
        if not host_part:
            raise ValueError("odoo_url must include a hostname")

        # Strip trailing slashes after validation
        v = v.rstrip("/")

        return v

    @field_validator("odoo_db")
    @classmethod
    def validate_odoo_db(cls, v: str) -> str:
        """Database name: alphanumeric, underscore, hyphen, dot only."""
        if v and not _DB_NAME_PATTERN.match(v):
            raise ValueError(
                "odoo_db may only contain alphanumeric characters, "
                "underscores, hyphens, and dots"
            )
        return v
