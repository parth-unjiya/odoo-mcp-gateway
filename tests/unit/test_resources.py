"""Tests for MCP Resource handlers."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from odoo_mcp_gateway.core.discovery.field_inspector import FieldInspector
from odoo_mcp_gateway.core.discovery.model_registry import ModelRegistry
from odoo_mcp_gateway.core.discovery.models import (
    AccessLevel,
    FieldInfo,
    ModelInfo,
)
from odoo_mcp_gateway.resources.handlers import (
    _get_client,
    _is_admin,
    register_resources,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class _FakeRestrictions:
    """Mock restrictions that allow everything by default."""

    def check_model_access(
        self, model: str, operation: str, is_admin: bool
    ) -> str | None:
        return None  # Allow all


class _FakeRBAC:
    """Mock RBAC that allows everything."""

    def check_tool_access(
        self, tool_name: str, user_groups: list[str], is_admin: bool
    ) -> str | None:
        return None  # Allow all

    def get_visible_fields(
        self, model: str, user_groups: list[str], is_admin: bool
    ) -> set[str] | None:
        return None  # All visible

    def filter_response_fields(
        self,
        records: list[dict[str, Any]],
        model: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> list[dict[str, Any]]:
        return records  # No redaction


class _FakeContext:
    """Lightweight stand-in for GatewayContext used in resource tests."""

    def __init__(
        self,
        *,
        models: dict[str, ModelInfo] | None = None,
        auth_mgr: Any = None,
        field_inspector: FieldInspector | None = None,
        restrictions: Any = None,
        rbac: Any = None,
    ) -> None:
        self.model_registry = ModelRegistry()
        if models:
            self.model_registry._models = models
        self.field_inspector = field_inspector or FieldInspector()
        self.restrictions = restrictions or _FakeRestrictions()
        self.rbac = rbac or _FakeRBAC()
        self.auth_managers: dict[str, Any] = {}
        if auth_mgr is not None:
            self.auth_managers["session"] = auth_mgr
        # Disable security_gate sub-checks for resource tests
        self.rate_limiter = None
        self.audit_logger = None


def _make_auth_mgr(
    *,
    is_admin: bool = False,
    client: Any = None,
    raises: bool = False,
) -> MagicMock:
    mgr = MagicMock()
    auth_result = MagicMock()
    auth_result.is_admin = is_admin
    auth_result.groups = []
    mgr.auth_result = auth_result
    if raises:
        mgr.get_active_client.side_effect = RuntimeError("no client")
    else:
        mgr.get_active_client.return_value = client or AsyncMock()
    return mgr


def _model(
    name: str,
    description: str = "",
    is_custom: bool = False,
    access_level: AccessLevel = AccessLevel.FULL_CRUD,
) -> ModelInfo:
    return ModelInfo(
        name=name,
        description=description or name,
        is_custom=is_custom,
        is_transient=False,
        module="base",
        state="base",
        access_level=access_level,
    )


# We capture the inner handler functions via a thin wrapper around
# server.resource() so we can call them directly in tests without
# depending on FastMCP's internal resource manager key scheme.
_captured: dict[str, Any] = {}


def _register(ctx: _FakeContext) -> dict[str, Any]:
    """Register resources and return a dict of handler callables.

    Keys are the URI template strings passed to ``@server.resource()``.
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    captured: dict[str, Any] = {}
    original_resource = server.resource

    def capturing_resource(uri: str, **kwargs: Any):  # type: ignore[no-untyped-def]
        decorator = original_resource(uri, **kwargs)

        def wrapper(fn: Any) -> Any:
            result = decorator(fn)
            captured[uri] = fn
            return result

        return wrapper

    server.resource = capturing_resource  # type: ignore[assignment]
    register_resources(server, lambda: ctx)
    return captured


# ------------------------------------------------------------------
# list_models_resource
# ------------------------------------------------------------------


async def test_list_models_resource_returns_json() -> None:
    ctx = _FakeContext(
        models={
            "res.partner": _model("res.partner", "Contact"),
        },
        auth_mgr=_make_auth_mgr(),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models"]()
    data = json.loads(raw)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["model"] == "res.partner"
    assert data[0]["description"] == "Contact"


async def test_list_models_resource_includes_custom() -> None:
    ctx = _FakeContext(
        models={
            "res.partner": _model("res.partner", "Contact"),
            "x_custom.model": _model("x_custom.model", "Custom", is_custom=True),
        },
        auth_mgr=_make_auth_mgr(),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models"]()
    data = json.loads(raw)
    names = [d["model"] for d in data]
    assert "x_custom.model" in names
    custom = [d for d in data if d["model"] == "x_custom.model"][0]
    assert custom["is_custom"] is True


# ------------------------------------------------------------------
# model_detail_resource
# ------------------------------------------------------------------


async def test_model_detail_returns_fields() -> None:
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
            ),
        },
    )
    ctx = _FakeContext(
        models={"res.partner": _model("res.partner", "Contact")},
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert data["model"] == "res.partner"
    assert "name" in data["fields"]
    assert data["fields"]["name"]["type"] == "char"
    assert data["field_count"] == 1


async def test_model_detail_unknown_model() -> None:
    ctx = _FakeContext(models={}, auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="no.such.model")
    data = json.loads(raw)
    assert "error" in data
    assert "not found" in data["error"]


async def test_model_detail_without_auth() -> None:
    """model_detail_resource now returns auth error when not authenticated."""
    ctx = _FakeContext(
        models={"res.partner": _model("res.partner", "Contact")},
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert "error" in data
    assert "Not authenticated" in data["error"] or "login" in data["error"].lower()


# ------------------------------------------------------------------
# record_resource
# ------------------------------------------------------------------


async def test_record_resource_returns_data() -> None:
    mock_client = AsyncMock()
    mock_client.execute_kw.return_value = [{"id": 1, "name": "Test Partner"}]
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(client=mock_client))
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="1"
    )
    data = json.loads(raw)
    assert data["id"] == 1
    assert data["name"] == "Test Partner"


async def test_record_resource_not_found() -> None:
    mock_client = AsyncMock()
    mock_client.execute_kw.return_value = []
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(client=mock_client))
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="9999"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "not found" in data["error"]


async def test_record_resource_invalid_id() -> None:
    ctx = _FakeContext(auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="abc"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Invalid record ID" in data["error"]


async def test_record_resource_without_auth() -> None:
    ctx = _FakeContext()
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="1"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Not authenticated" in data["error"]


# ------------------------------------------------------------------
# schema_resource
# ------------------------------------------------------------------


async def test_schema_resource_returns_field_schema() -> None:
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
            ),
            "email": FieldInfo(
                name="email",
                field_type="char",
                string="Email",
                required=False,
            ),
        },
    )
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert data["model"] == "res.partner"
    assert data["total_fields"] == 2
    assert "name" in data["fields"]
    assert data["fields"]["name"]["type"] == "char"
    assert data["fields"]["name"]["label"] == "Name"
    assert data["fields"]["name"]["required"] is True


async def test_schema_resource_marks_important_fields() -> None:
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
                store=True,
            ),
            "some_internal": FieldInfo(
                name="some_internal",
                field_type="char",
                string="Internal",
                store=True,
            ),
        },
    )
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    # "name" is a priority field so it should be important
    assert data["fields"]["name"]["important"] is True
    assert "name" in data["important_fields"]


# ------------------------------------------------------------------
# categories_resource
# ------------------------------------------------------------------


async def test_categories_resource_returns_counts() -> None:
    ctx = _FakeContext(
        models={
            "sale.order": _model("sale.order", "Sales Order"),
            "hr.employee": _model("hr.employee", "Employee"),
        },
        auth_mgr=_make_auth_mgr(is_admin=True),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://categories"]()
    data = json.loads(raw)
    assert isinstance(data, dict)
    # sale.order should match "sales" category keyword "sale"
    assert data.get("sales", 0) >= 1


# ------------------------------------------------------------------
# Restriction enforcement
# ------------------------------------------------------------------


class _BlockingRestrictions:
    """Mock restrictions that block a specific model."""

    def __init__(self, blocked: str) -> None:
        self._blocked = blocked

    def check_model_access(
        self, model: str, operation: str, is_admin: bool
    ) -> str | None:
        if model == self._blocked:
            return f"Model '{model}' is not accessible"
        return None


async def test_record_resource_blocked_model() -> None:
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        restrictions=_BlockingRestrictions("ir.config_parameter"),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="ir.config_parameter", record_id="1"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "not accessible" in data["error"]


async def test_schema_resource_blocked_model() -> None:
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        restrictions=_BlockingRestrictions("ir.config_parameter"),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](
        model_name="ir.config_parameter",
    )
    data = json.loads(raw)
    assert "error" in data
    assert "not accessible" in data["error"]


async def test_model_detail_blocked_model() -> None:
    ctx = _FakeContext(
        models={"ir.config_parameter": _model("ir.config_parameter")},
        auth_mgr=_make_auth_mgr(),
        restrictions=_BlockingRestrictions("ir.config_parameter"),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](
        model_name="ir.config_parameter",
    )
    data = json.loads(raw)
    assert "error" in data
    assert "not accessible" in data["error"]


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------


async def test_is_admin_helper_no_auth() -> None:
    ctx = _FakeContext()
    assert _is_admin(ctx) is False


async def test_is_admin_helper_with_admin() -> None:
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(is_admin=True))
    assert _is_admin(ctx) is True


async def test_is_admin_helper_with_non_admin() -> None:
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(is_admin=False))
    assert _is_admin(ctx) is False


async def test_get_client_helper_no_auth() -> None:
    ctx = _FakeContext()
    assert _get_client(ctx) is None


async def test_get_client_helper_with_auth() -> None:
    mock_client = AsyncMock()
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(client=mock_client))
    assert _get_client(ctx) is mock_client


async def test_get_client_helper_raises_returns_none() -> None:
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(raises=True))
    assert _get_client(ctx) is None


# ------------------------------------------------------------------
# Model name normalization
# ------------------------------------------------------------------


async def test_model_detail_normalizes_name() -> None:
    """model_detail_resource normalizes 'RES.PARTNER ' to 'res.partner'."""
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
            ),
        },
    )
    ctx = _FakeContext(
        models={"res.partner": _model("res.partner", "Contact")},
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="RES.PARTNER ")
    data = json.loads(raw)
    assert data["model"] == "res.partner"
    assert "name" in data["fields"]


async def test_record_resource_normalizes_name() -> None:
    """record_resource normalizes 'RES.PARTNER ' to 'res.partner'."""
    mock_client = AsyncMock()
    mock_client.execute_kw.return_value = [{"id": 1, "name": "Test"}]
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(client=mock_client))
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="RES.PARTNER ", record_id="1"
    )
    data = json.loads(raw)
    assert data["id"] == 1
    assert data["name"] == "Test"
    # Verify the client was called with the normalized name
    mock_client.execute_kw.assert_called_once_with("res.partner", "read", [[1]], {})


async def test_schema_resource_normalizes_name() -> None:
    """schema_resource normalizes 'RES.PARTNER ' to 'res.partner'."""
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "email": FieldInfo(
                name="email",
                field_type="char",
                string="Email",
                required=False,
            ),
        },
    )
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](model_name="RES.PARTNER ")
    data = json.loads(raw)
    assert data["model"] == "res.partner"
    assert "email" in data["fields"]


# ------------------------------------------------------------------
# RBAC field filtering on resources
# ------------------------------------------------------------------


class _FieldHidingRBAC:
    """RBAC mock that hides specific fields from model detail."""

    def __init__(self, hidden_fields: set[str]) -> None:
        self._hidden = hidden_fields

    def check_tool_access(
        self, tool_name: str, user_groups: list[str], is_admin: bool
    ) -> str | None:
        return None

    def get_visible_fields(
        self, model: str, user_groups: list[str], is_admin: bool
    ) -> set[str] | None:
        return self._hidden

    def filter_response_fields(
        self,
        records: list[dict[str, Any]],
        model: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> list[dict[str, Any]]:
        return records  # No redaction in this mock


class _RecordRedactingRBAC:
    """RBAC mock that redacts specific fields from record responses."""

    def __init__(self, redacted_field: str) -> None:
        self._redacted = redacted_field

    def check_tool_access(
        self, tool_name: str, user_groups: list[str], is_admin: bool
    ) -> str | None:
        return None

    def get_visible_fields(
        self, model: str, user_groups: list[str], is_admin: bool
    ) -> set[str] | None:
        return None  # All visible in metadata

    def filter_response_fields(
        self,
        records: list[dict[str, Any]],
        model: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> list[dict[str, Any]]:
        return [
            {k: "***" if k == self._redacted else v for k, v in rec.items()}
            for rec in records
        ]


async def test_model_detail_rbac_hides_fields() -> None:
    """RBAC get_visible_fields excludes 'secret_field' from model detail."""
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
            ),
            "secret_field": FieldInfo(
                name="secret_field",
                field_type="char",
                string="Secret",
                required=False,
            ),
            "email": FieldInfo(
                name="email",
                field_type="char",
                string="Email",
                required=False,
            ),
        },
    )
    rbac = _FieldHidingRBAC(hidden_fields={"secret_field"})
    ctx = _FakeContext(
        models={"res.partner": _model("res.partner", "Contact")},
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
        rbac=rbac,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="res.partner")
    data = json.loads(raw)

    assert "secret_field" not in data["fields"]
    assert "name" in data["fields"]
    assert "email" in data["fields"]
    # field_count reflects post-RBAC filtering
    assert data["field_count"] == 2


async def test_record_resource_rbac_filters_fields() -> None:
    """RBAC filter_response_fields redacts 'secret' from record data."""
    mock_client = AsyncMock()
    mock_client.execute_kw.return_value = [
        {"id": 1, "name": "Alice", "secret": "password123"}
    ]
    rbac = _RecordRedactingRBAC(redacted_field="secret")
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(client=mock_client),
        rbac=rbac,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="1"
    )
    data = json.loads(raw)

    assert data["id"] == 1
    assert data["name"] == "Alice"
    assert data["secret"] == "***"


# ------------------------------------------------------------------
# record_resource error paths
# ------------------------------------------------------------------


async def test_record_resource_negative_id() -> None:
    """record_resource rejects record ID <= 0."""
    ctx = _FakeContext(auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="0"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "positive" in data["error"].lower()


async def test_record_resource_negative_record_id() -> None:
    """record_resource rejects negative record IDs."""
    ctx = _FakeContext(auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="-5"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "positive" in data["error"].lower()


async def test_record_resource_execute_kw_exception() -> None:
    """record_resource returns error when execute_kw raises."""
    mock_client = AsyncMock()
    mock_client.execute_kw.side_effect = RuntimeError("Connection lost")
    ctx = _FakeContext(auth_mgr=_make_auth_mgr(client=mock_client))
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="1"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Failed to read record" in data["error"]
    assert "RuntimeError" in data["error"]


async def test_record_resource_invalid_model_name() -> None:
    """record_resource rejects invalid model names."""
    ctx = _FakeContext(auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="123-invalid!", record_id="1"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Invalid model name" in data["error"]


async def test_record_resource_model_restricted() -> None:
    """record_resource returns error for restricted model access."""
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        restrictions=_BlockingRestrictions("res.users"),
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.users", record_id="1"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "not accessible" in data["error"]


# ------------------------------------------------------------------
# schema_resource error paths
# ------------------------------------------------------------------


async def test_schema_resource_invalid_model_name() -> None:
    """schema_resource rejects invalid model names."""
    ctx = _FakeContext(auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](
        model_name="INVALID MODEL!"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Invalid model name" in data["error"]


async def test_schema_resource_without_auth() -> None:
    """schema_resource returns error when not authenticated."""
    ctx = _FakeContext()
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](
        model_name="res.partner"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Not authenticated" in data["error"]


async def test_schema_resource_with_selection_and_help() -> None:
    """schema_resource includes selection options and help text."""
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "type": FieldInfo(
                name="type",
                field_type="selection",
                string="Type",
                required=False,
                selection=[("contact", "Contact"), ("invoice", "Invoice")],
                help_text="Address type",
            ),
        },
    )
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert "type" in data["fields"]
    assert data["fields"]["type"]["options"] == [
        ["contact", "Contact"],
        ["invoice", "Invoice"],
    ]
    assert data["fields"]["type"]["help"] == "Address type"


async def test_schema_resource_rbac_hides_fields() -> None:
    """schema_resource applies RBAC to hide sensitive fields."""
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
            ),
            "credit_limit": FieldInfo(
                name="credit_limit",
                field_type="float",
                string="Credit Limit",
                required=False,
            ),
        },
    )
    rbac = _FieldHidingRBAC(hidden_fields={"credit_limit"})
    ctx = _FakeContext(
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
        rbac=rbac,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert "name" in data["fields"]
    assert "credit_limit" not in data["fields"]


# ------------------------------------------------------------------
# model_detail_resource error paths
# ------------------------------------------------------------------


async def test_model_detail_invalid_model_name() -> None:
    """model_detail_resource rejects invalid model names."""
    ctx = _FakeContext(auth_mgr=_make_auth_mgr())
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](
        model_name="DROP TABLE users"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Invalid model name" in data["error"]


async def test_model_detail_binary_fields_excluded() -> None:
    """model_detail_resource excludes binary fields from the response."""
    inspector = FieldInspector()
    inspector._cache["res.partner"] = (
        999999999.0,
        {
            "name": FieldInfo(
                name="name",
                field_type="char",
                string="Name",
                required=True,
            ),
            "image_1920": FieldInfo(
                name="image_1920",
                field_type="binary",
                string="Image",
                required=False,
                is_binary=True,
            ),
        },
    )
    ctx = _FakeContext(
        models={"res.partner": _model("res.partner", "Contact")},
        auth_mgr=_make_auth_mgr(),
        field_inspector=inspector,
    )
    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert "name" in data["fields"]
    assert "image_1920" not in data["fields"]


# ------------------------------------------------------------------
# list_models_resource error paths
# ------------------------------------------------------------------


async def test_list_models_resource_exception() -> None:
    """list_models_resource returns error JSON when get_context raises."""

    def bad_context():
        raise RuntimeError("Context init failed")

    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    captured: dict[str, Any] = {}
    original_resource = server.resource

    def capturing_resource(uri: str, **kwargs: Any):
        decorator = original_resource(uri, **kwargs)

        def wrapper(fn: Any) -> Any:
            result = decorator(fn)
            captured[uri] = fn
            return result

        return wrapper

    server.resource = capturing_resource  # type: ignore[assignment]
    register_resources(server, bad_context)

    raw = await captured["odoo://models"]()
    data = json.loads(raw)
    assert "error" in data
    assert "Failed to list models" in data["error"]


# ------------------------------------------------------------------
# categories_resource error paths
# ------------------------------------------------------------------


async def test_categories_resource_exception() -> None:
    """categories_resource returns error JSON on exception."""

    def bad_context():
        raise RuntimeError("Boom")

    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="test")
    captured: dict[str, Any] = {}
    original_resource = server.resource

    def capturing_resource(uri: str, **kwargs: Any):
        decorator = original_resource(uri, **kwargs)

        def wrapper(fn: Any) -> Any:
            result = decorator(fn)
            captured[uri] = fn
            return result

        return wrapper

    server.resource = capturing_resource  # type: ignore[assignment]
    register_resources(server, bad_context)

    raw = await captured["odoo://categories"]()
    data = json.loads(raw)
    assert "error" in data
    assert "Failed to list categories" in data["error"]


# ------------------------------------------------------------------
# _get_auth_result edge cases
# ------------------------------------------------------------------


async def test_get_auth_result_no_auth_managers() -> None:
    """_get_auth_result returns None when no auth managers exist."""
    from odoo_mcp_gateway.resources.handlers import _get_auth_result

    ctx = _FakeContext()
    assert _get_auth_result(ctx) is None


async def test_get_auth_result_no_auth_result_attr() -> None:
    """_get_auth_result returns None if auth_mgr has no auth_result attr."""
    from odoo_mcp_gateway.resources.handlers import _get_auth_result

    mgr = MagicMock(spec=[])  # No attributes at all
    ctx = _FakeContext()
    ctx.auth_managers["session"] = mgr
    result = _get_auth_result(ctx)
    assert result is None


# ------------------------------------------------------------------
# Gate error paths on resources
# ------------------------------------------------------------------


async def test_record_resource_gate_error() -> None:
    """record_resource returns error when _resource_gate blocks access."""
    mock_client = AsyncMock()
    auth_mgr = _make_auth_mgr(client=mock_client)
    ctx = _FakeContext(auth_mgr=auth_mgr)
    # Simulate rate limiter blocking
    ctx.rate_limiter = MagicMock()
    ctx.rate_limiter.check.return_value = (False, "Rate limit exceeded")
    ctx.audit_logger = None

    handlers = _register(ctx)
    raw = await handlers["odoo://record/{model_name}/{record_id}"](
        model_name="res.partner", record_id="1"
    )
    data = json.loads(raw)
    assert "error" in data
    assert "Rate limit" in data["error"]


async def test_schema_resource_gate_error() -> None:
    """schema_resource returns error when _resource_gate blocks access."""
    auth_mgr = _make_auth_mgr()
    ctx = _FakeContext(auth_mgr=auth_mgr)
    ctx.rate_limiter = MagicMock()
    ctx.rate_limiter.check.return_value = (False, "Rate limit exceeded")
    ctx.audit_logger = None

    handlers = _register(ctx)
    raw = await handlers["odoo://schema/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert "error" in data
    assert "Rate limit" in data["error"]


async def test_model_detail_gate_error() -> None:
    """model_detail_resource returns error when gate blocks."""
    auth_mgr = _make_auth_mgr()
    ctx = _FakeContext(
        models={"res.partner": _model("res.partner", "Contact")},
        auth_mgr=auth_mgr,
    )
    ctx.rate_limiter = MagicMock()
    ctx.rate_limiter.check.return_value = (False, "Rate limit exceeded")
    ctx.audit_logger = None

    handlers = _register(ctx)
    raw = await handlers["odoo://models/{model_name}"](model_name="res.partner")
    data = json.loads(raw)
    assert "error" in data
    assert "Rate limit" in data["error"]


# ------------------------------------------------------------------
# workflow_resource tests
# ------------------------------------------------------------------


class TestWorkflowResource:
    """Tests for the odoo://workflow/{model_name} resource handler."""

    async def test_valid_model_with_workflow_returns_definition(self) -> None:
        """workflow_resource returns workflow definition when available."""
        ctx = _FakeContext(auth_mgr=_make_auth_mgr())
        wf_data = {
            "model": "sale.order",
            "states": [
                {"name": "draft", "label": "Quotation"},
                {"name": "sale", "label": "Sales Order"},
            ],
            "transitions": [
                {"from": "draft", "to": "sale", "method": "action_confirm"},
            ],
        }
        wf_registry = MagicMock()
        wf_registry.to_dict.return_value = wf_data
        ctx.workflow_registry = wf_registry

        handlers = _register(ctx)
        raw = await handlers["odoo://workflow/{model_name}"](model_name="sale.order")
        data = json.loads(raw)
        assert data["model"] == "sale.order"
        assert len(data["states"]) == 2
        assert len(data["transitions"]) == 1
        assert data["transitions"][0]["method"] == "action_confirm"

    async def test_valid_model_without_workflow_returns_not_found(self) -> None:
        """workflow_resource returns error when no workflow is defined for model."""
        ctx = _FakeContext(auth_mgr=_make_auth_mgr())
        wf_registry = MagicMock()
        wf_registry.to_dict.return_value = None
        ctx.workflow_registry = wf_registry

        handlers = _register(ctx)
        raw = await handlers["odoo://workflow/{model_name}"](model_name="res.partner")
        data = json.loads(raw)
        assert "error" in data
        assert "No workflow definition found" in data["error"]

    async def test_invalid_model_name_format_returns_error(self) -> None:
        """workflow_resource rejects invalid model name format."""
        ctx = _FakeContext(auth_mgr=_make_auth_mgr())
        wf_registry = MagicMock()
        ctx.workflow_registry = wf_registry

        handlers = _register(ctx)
        raw = await handlers["odoo://workflow/{model_name}"](
            model_name="INVALID MODEL!",
        )
        data = json.loads(raw)
        assert "error" in data
        assert "Invalid model name" in data["error"]

    async def test_no_workflow_registry_returns_not_available(self) -> None:
        """workflow_resource returns error when workflow_registry is missing."""
        ctx = _FakeContext(auth_mgr=_make_auth_mgr())
        # Do NOT set ctx.workflow_registry -- _FakeContext does not have it

        handlers = _register(ctx)
        raw = await handlers["odoo://workflow/{model_name}"](model_name="sale.order")
        data = json.loads(raw)
        assert "error" in data
        assert "Workflow engine not available" in data["error"]

    async def test_rate_limit_blocks_workflow_resource(self) -> None:
        """workflow_resource returns error when rate limiter blocks access."""
        auth_mgr = _make_auth_mgr()
        ctx = _FakeContext(auth_mgr=auth_mgr)
        ctx.rate_limiter = MagicMock()
        ctx.rate_limiter.check.return_value = (False, "Rate limit exceeded")
        ctx.audit_logger = None
        wf_registry = MagicMock()
        wf_registry.to_dict.return_value = {"model": "sale.order"}
        ctx.workflow_registry = wf_registry

        handlers = _register(ctx)
        raw = await handlers["odoo://workflow/{model_name}"](model_name="sale.order")
        data = json.loads(raw)
        assert "error" in data
        assert "Rate limit" in data["error"]
