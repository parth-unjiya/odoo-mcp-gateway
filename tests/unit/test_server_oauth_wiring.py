"""Tests for _build_fastmcp's OAuth wiring (audit blocker #3).

The OAuth verifier classes (``OAuthJwtVerifier``, ``CompositeTokenVerifier``)
were importable and unit-tested in v0.3.0-dev but never actually wired
into the FastMCP server — only ``OdooTokenVerifier`` was instantiated.
That meant ``MCP_OAUTH_*`` env vars had zero effect on a running gateway.

These tests pin the new behaviour:

* Defaults (``oauth_enabled=False``) → ``OdooTokenVerifier`` (unchanged).
* ``oauth_enabled=True`` with issuer+audience → ``CompositeTokenVerifier``
  containing BOTH an ``OdooTokenVerifier`` (so opaque tokens keep
  working) AND an ``OAuthJwtVerifier``.
* ``oauth_enabled=True`` without issuer or audience → fail-fast
  ``ConfigurationError`` at build time.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.auth.oauth_verifier import (
    OAUTH_AVAILABLE,
    CompositeTokenVerifier,
    OAuthJwtVerifier,
)
from odoo_mcp_gateway.core.auth.token_verifier import OdooTokenVerifier
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import (
    ConfigurationError,
    GatewayContext,
    _build_fastmcp,
)


def _make_gateway(**overrides: Any) -> GatewayContext:
    settings_defaults: dict[str, Any] = {
        "odoo_url": "http://localhost:8069",
        "odoo_db": "testdb",
        "odoo_username": "",
        "odoo_api_key": SecretStr(""),
        "mcp_transport": "streamable-http",
    }
    settings_defaults.update(overrides)
    settings = Settings(**settings_defaults)
    config = GatewayConfig(
        restrictions=RestrictionConfig(),
        rbac=RBACConfig(),
        model_access=ModelAccessConfig(),
    )
    return settings, GatewayContext(settings, config)  # type: ignore[return-value]


def _extract_verifier(server: Any) -> Any:
    """Pull the configured token_verifier off the FastMCP instance.

    FastMCP stashes the verifier on a private attribute; we use the
    same attribute the SDK reads internally. Falls back to None if
    the attribute path changes between SDK versions.
    """
    # The SDK currently exposes it as ``_token_verifier`` on the
    # FastMCP instance — covering the few likely names defensively.
    for attr in ("_token_verifier", "token_verifier", "_auth_token_verifier"):
        value = getattr(server, attr, None)
        if value is not None:
            return value
    return None


class TestDefaultsOpaqueOnly:
    """oauth_enabled=False → OdooTokenVerifier only (backwards compat)."""

    def test_default_settings_wire_odoo_token_verifier(self) -> None:
        settings, gateway = _make_gateway()
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        assert verifier is not None, "FastMCP must have a token_verifier in HTTP mode"
        assert isinstance(verifier, OdooTokenVerifier), (
            f"Default config must wire OdooTokenVerifier, got {type(verifier).__name__}"
        )


class TestOAuthEnabled:
    """oauth_enabled=True → CompositeTokenVerifier(OdooTokenVerifier + JWT)."""

    @pytest.mark.skipif(
        not OAUTH_AVAILABLE,
        reason="OAuth extra (authlib) not installed",
    )
    def test_full_config_wires_composite(self) -> None:
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer="https://keycloak.example.com/realms/odoo",
            oauth_audience="https://mcp.example.com/",
        )
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        assert isinstance(verifier, CompositeTokenVerifier), (
            f"OAuth-enabled must wire CompositeTokenVerifier, "
            f"got {type(verifier).__name__}"
        )
        # The composite must contain BOTH delegates so opaque tokens
        # keep working alongside JWTs.
        delegates = verifier._delegates  # noqa: SLF001
        delegate_types = {type(d) for d in delegates}
        assert OdooTokenVerifier in delegate_types, (
            "Composite is missing the opaque-token verifier — opaque "
            "tokens issued by the gateway's login tool would stop working"
        )
        assert OAuthJwtVerifier in delegate_types, (
            "Composite is missing the JWT verifier — OAuth is wired but "
            "JWTs would never be validated"
        )

    @pytest.mark.skipif(
        not OAUTH_AVAILABLE,
        reason="OAuth extra (authlib) not installed",
    )
    def test_opaque_first_ordering(self) -> None:
        """Opaque verifier MUST come first in the composite delegate list.

        Opaque tokens are short-circuited by a single dict lookup;
        putting them first means JWT decoding overhead is only paid
        for tokens that aren't gateway-issued.
        """
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer="https://idp.example.com/realms/odoo",
            oauth_audience="https://mcp.example.com/",
        )
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        assert isinstance(verifier, CompositeTokenVerifier)
        delegates = verifier._delegates  # noqa: SLF001
        assert len(delegates) == 2
        assert isinstance(delegates[0], OdooTokenVerifier), (
            "First delegate must be the opaque verifier (cheap dict lookup)"
        )
        assert isinstance(delegates[1], OAuthJwtVerifier)

    @pytest.mark.skipif(
        not OAUTH_AVAILABLE,
        reason="OAuth extra (authlib) not installed",
    )
    def test_jwks_uri_derived_when_unset(self) -> None:
        """Without an explicit jwks_uri, derive from issuer."""
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer="https://idp.example.com/realms/odoo",
            oauth_audience="https://mcp.example.com/",
        )
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        oauth = next(
            d
            for d in verifier._delegates  # noqa: SLF001
            if isinstance(d, OAuthJwtVerifier)
        )
        assert oauth._jwks_uri == (  # noqa: SLF001
            "https://idp.example.com/realms/odoo/.well-known/jwks.json"
        )

    @pytest.mark.skipif(
        not OAUTH_AVAILABLE,
        reason="OAuth extra (authlib) not installed",
    )
    def test_explicit_jwks_uri_wins(self) -> None:
        """Explicit oauth_jwks_uri overrides the issuer-derived default."""
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer="https://idp.example.com/realms/odoo",
            oauth_audience="https://mcp.example.com/",
            oauth_jwks_uri="https://custom.example.com/keys",
        )
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        oauth = next(
            d
            for d in verifier._delegates  # noqa: SLF001
            if isinstance(d, OAuthJwtVerifier)
        )
        assert oauth._jwks_uri == "https://custom.example.com/keys"  # noqa: SLF001

    @pytest.mark.skipif(
        not OAUTH_AVAILABLE,
        reason="OAuth extra (authlib) not installed",
    )
    def test_required_scopes_parsed(self) -> None:
        """Comma-separated scopes are split, stripped, and de-emptied."""
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer="https://idp.example.com/realms/odoo",
            oauth_audience="https://mcp.example.com/",
            oauth_required_scopes=" odoo.read,  odoo.write,,odoo.admin",
        )
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        oauth = next(
            d
            for d in verifier._delegates  # noqa: SLF001
            if isinstance(d, OAuthJwtVerifier)
        )
        assert oauth._required_scopes == [  # noqa: SLF001
            "odoo.read",
            "odoo.write",
            "odoo.admin",
        ]


class TestMisconfiguration:
    """Fail-fast when OAuth is enabled but required fields are missing."""

    def test_enabled_without_issuer_raises(self) -> None:
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer=None,
            oauth_audience="https://mcp.example.com/",
        )
        with pytest.raises(ConfigurationError) as exc_info:
            _build_fastmcp(settings, gateway)
        assert "OAUTH_ISSUER" in str(exc_info.value)

    def test_enabled_without_audience_raises(self) -> None:
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer="https://idp.example.com/realms/odoo",
            oauth_audience=None,
        )
        with pytest.raises(ConfigurationError) as exc_info:
            _build_fastmcp(settings, gateway)
        assert "OAUTH_AUDIENCE" in str(exc_info.value)

    def test_enabled_without_either_raises(self) -> None:
        settings, gateway = _make_gateway(
            oauth_enabled=True,
            oauth_issuer=None,
            oauth_audience=None,
        )
        with pytest.raises(ConfigurationError):
            _build_fastmcp(settings, gateway)

    def test_disabled_with_partial_config_does_not_raise(self) -> None:
        """If oauth_enabled=False, missing issuer/audience is fine.

        The fields are advertised in YAML for documentation but the
        operator may have left them blank intentionally. The build
        path must not punish that.
        """
        settings, gateway = _make_gateway(
            oauth_enabled=False,
            oauth_issuer=None,
            oauth_audience=None,
        )
        # Must NOT raise.
        server = _build_fastmcp(settings, gateway)
        verifier = _extract_verifier(server)
        assert isinstance(verifier, OdooTokenVerifier)


class TestStdioMode:
    """Stdio mode never wires bearer auth — OAuth flags are no-ops there."""

    def test_stdio_ignores_oauth_settings(self) -> None:
        settings, gateway = _make_gateway(
            mcp_transport="stdio",
            oauth_enabled=True,  # should be ignored entirely
            oauth_issuer=None,  # would normally raise, but stdio never reads it
        )
        # Must not raise — stdio path returns early.
        server = _build_fastmcp(settings, gateway)
        # And no verifier is wired.
        verifier = _extract_verifier(server)
        assert verifier is None
