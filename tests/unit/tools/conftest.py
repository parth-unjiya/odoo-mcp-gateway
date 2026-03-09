"""Shared fixtures for tool tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from odoo_mcp_gateway.client.base import AuthResult, OdooClientBase
from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.core.auth.manager import AuthManager
from odoo_mcp_gateway.core.security.config_loader import (
    GatewayConfig,
    ModelAccessConfig,
    RBACConfig,
    RestrictionConfig,
)
from odoo_mcp_gateway.server import GatewayContext


def make_auth_result(**overrides: Any) -> AuthResult:
    """Create an AuthResult with sensible defaults."""
    defaults: dict[str, Any] = {
        "uid": 1,
        "session_id": "s1",
        "user_context": {"lang": "en_US"},
        "is_admin": False,
        "groups": ["base.group_user"],
        "username": "testuser",
        "database": "testdb",
    }
    defaults.update(overrides)
    return AuthResult(**defaults)


def make_mock_client(execute_kw_return: Any = None) -> AsyncMock:
    """Create a mock OdooClientBase."""
    client = AsyncMock(spec=OdooClientBase)
    client.execute_kw = AsyncMock(return_value=execute_kw_return)
    return client


def make_gateway(
    restriction_config: RestrictionConfig | None = None,
    model_access_config: ModelAccessConfig | None = None,
    rbac_config: RBACConfig | None = None,
    auth_result: AuthResult | None = None,
    mock_client: AsyncMock | None = None,
    is_admin: bool = False,
    user_groups: list[str] | None = None,
) -> GatewayContext:
    """Create a GatewayContext with an authenticated mock client."""
    settings = Settings(
        odoo_url="http://localhost:8069",
        odoo_db="testdb",
    )
    config = GatewayConfig(
        restrictions=restriction_config or RestrictionConfig(),
        rbac=rbac_config or RBACConfig(),
        model_access=model_access_config
        or ModelAccessConfig(
            default_policy="allow",
            stock_models={
                "full_crud": [
                    "res.partner",
                    "sale.order",
                    "crm.lead",
                    "hr.employee",
                    "account.move",
                ],
            },
        ),
    )
    gateway = GatewayContext(settings, config)

    # Set up mock auth manager and client
    if auth_result is None:
        auth_result = make_auth_result(
            is_admin=is_admin,
            groups=user_groups or ["base.group_user"],
        )

    if mock_client is None:
        mock_client = make_mock_client()

    auth_mgr = MagicMock(spec=AuthManager)
    auth_mgr.get_active_client.return_value = mock_client
    auth_mgr.auth_result = auth_result

    gateway.auth_managers["test_session"] = auth_mgr

    return gateway


@pytest.fixture()
def default_gateway() -> GatewayContext:
    """A GatewayContext with default allow-all config and mock client."""
    return make_gateway()
