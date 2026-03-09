"""Tests for the RBAC manager."""

from __future__ import annotations

import pytest

from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RBACConfig,
)
from odoo_mcp_gateway.core.security.rbac import RBACManager


@pytest.fixture()
def rbac_config() -> RBACConfig:
    return RBACConfig(
        tool_group_requirements={
            "delete_record": ["base.group_system"],
            "execute_method": ["base.group_system"],
            "update_record": ["base.group_user"],
            "create_record": ["base.group_user"],
        },
        sensitive_fields={
            "hr.employee": {
                "fields": ["wage", "salary", "ssnid"],
                "required_group": "hr.group_hr_manager",
            },
            "res.users": {
                "fields": ["password", "signup_token"],
                "required_group": "base.group_system",
            },
        },
        field_group_overrides={
            "account.move": {
                "amount_total": {
                    "read": "account.group_account_invoice",
                    "write": "account.group_account_manager",
                },
            },
            "sale.order": {
                "margin": {
                    "read": "sale.group_sale_manager",
                    "write": "sale.group_sale_manager",
                },
            },
        },
    )


@pytest.fixture()
def model_access() -> ModelAccessConfig:
    return ModelAccessConfig(
        sensitive_fields={
            "res.partner": ["vat", "bank_ids"],
            "hr.employee": ["wage", "salary"],
        },
    )


@pytest.fixture()
def rbac(rbac_config: RBACConfig, model_access: ModelAccessConfig) -> RBACManager:
    return RBACManager(rbac_config, model_access)


# ── check_tool_access ──────────────────────────────────────────────


class TestToolAccess:
    def test_admin_always_allowed(self, rbac: RBACManager) -> None:
        msg = rbac.check_tool_access("delete_record", [], True)
        assert msg is None

    def test_non_admin_with_required_group(self, rbac: RBACManager) -> None:
        msg = rbac.check_tool_access("update_record", ["base.group_user"], False)
        assert msg is None

    def test_non_admin_without_required_group(self, rbac: RBACManager) -> None:
        msg = rbac.check_tool_access("delete_record", ["base.group_user"], False)
        assert msg is not None
        assert "requires one of" in msg

    def test_unrestricted_tool(self, rbac: RBACManager) -> None:
        msg = rbac.check_tool_access("search_read", [], False)
        assert msg is None

    def test_empty_user_groups(self, rbac: RBACManager) -> None:
        msg = rbac.check_tool_access("create_record", [], False)
        assert msg is not None

    def test_multiple_required_groups_any_match(self) -> None:
        config = RBACConfig(
            tool_group_requirements={
                "test_tool": ["group_a", "group_b"],
            },
        )
        mgr = RBACManager(config, ModelAccessConfig())
        msg = mgr.check_tool_access("test_tool", ["group_b"], False)
        assert msg is None

    def test_multiple_required_groups_none_match(self) -> None:
        config = RBACConfig(
            tool_group_requirements={
                "test_tool": ["group_a", "group_b"],
            },
        )
        mgr = RBACManager(config, ModelAccessConfig())
        msg = mgr.check_tool_access("test_tool", ["group_c"], False)
        assert msg is not None


# ── filter_response_fields ─────────────────────────────────────────


class TestFilterResponseFields:
    def test_admin_sees_all_fields(self, rbac: RBACManager) -> None:
        records = [{"name": "Test", "wage": 5000, "salary": 60000}]
        result = rbac.filter_response_fields(records, "hr.employee", [], True)
        assert result[0]["wage"] == 5000

    def test_non_admin_sensitive_redacted(self, rbac: RBACManager) -> None:
        records = [{"name": "Test", "wage": 5000, "salary": 60000}]
        result = rbac.filter_response_fields(
            records, "hr.employee", ["base.group_user"], False
        )
        assert result[0]["wage"] == "***"
        assert result[0]["salary"] == "***"
        assert result[0]["name"] == "Test"

    def test_user_with_required_group_sees_fields(self, rbac: RBACManager) -> None:
        records = [{"name": "Test", "wage": 5000}]
        result = rbac.filter_response_fields(
            records, "hr.employee", ["hr.group_hr_manager"], False
        )
        # Has the required group for rbac sensitive_fields, but
        # model_access sensitive_fields also redacts wage
        assert result[0]["wage"] == "***"

    def test_model_access_sensitive_fields_redacted(self, rbac: RBACManager) -> None:
        records = [{"name": "Test", "vat": "NL123456789B01"}]
        result = rbac.filter_response_fields(
            records, "res.partner", ["base.group_user"], False
        )
        assert result[0]["vat"] == "***"

    def test_field_group_override_redacts(self, rbac: RBACManager) -> None:
        records = [{"name": "INV/001", "amount_total": 1500.0}]
        result = rbac.filter_response_fields(
            records, "account.move", ["base.group_user"], False
        )
        assert result[0]["amount_total"] == "***"

    def test_field_group_override_with_correct_group(self, rbac: RBACManager) -> None:
        records = [{"name": "INV/001", "amount_total": 1500.0}]
        result = rbac.filter_response_fields(
            records,
            "account.move",
            ["account.group_account_invoice"],
            False,
        )
        assert result[0]["amount_total"] == 1500.0

    def test_multiple_records_filtered(self, rbac: RBACManager) -> None:
        records = [
            {"id": 1, "wage": 5000},
            {"id": 2, "wage": 6000},
        ]
        result = rbac.filter_response_fields(records, "hr.employee", [], False)
        assert len(result) == 2
        assert all(r["wage"] == "***" for r in result)

    def test_no_sensitive_fields_for_model(self, rbac: RBACManager) -> None:
        records = [{"name": "Test", "code": "ABC"}]
        result = rbac.filter_response_fields(records, "product.category", [], False)
        assert result[0] == {"name": "Test", "code": "ABC"}

    def test_original_records_not_mutated(self, rbac: RBACManager) -> None:
        records = [{"name": "Test", "wage": 5000}]
        rbac.filter_response_fields(records, "hr.employee", [], False)
        assert records[0]["wage"] == 5000

    def test_empty_records_list(self, rbac: RBACManager) -> None:
        result = rbac.filter_response_fields([], "hr.employee", [], False)
        assert result == []


# ── sanitize_write_values ──────────────────────────────────────────


class TestSanitizeWriteValues:
    def test_admin_keeps_all_values(self, rbac: RBACManager) -> None:
        values = {"wage": 5000, "name": "Test"}
        result = rbac.sanitize_write_values(values, "hr.employee", [], True)
        assert result == {"wage": 5000, "name": "Test"}

    def test_non_admin_blocked_fields_removed(self, rbac: RBACManager) -> None:
        values = {"wage": 5000, "name": "Test"}
        result = rbac.sanitize_write_values(
            values, "hr.employee", ["base.group_user"], False
        )
        assert "wage" not in result
        assert result["name"] == "Test"

    def test_field_group_override_write_blocked(self, rbac: RBACManager) -> None:
        values = {"amount_total": 1500.0, "name": "INV/001"}
        result = rbac.sanitize_write_values(
            values, "account.move", ["account.group_account_invoice"], False
        )
        assert "amount_total" not in result
        assert result["name"] == "INV/001"

    def test_field_group_override_write_with_group(self, rbac: RBACManager) -> None:
        values = {"amount_total": 1500.0, "name": "INV/001"}
        result = rbac.sanitize_write_values(
            values, "account.move", ["account.group_account_manager"], False
        )
        assert result["amount_total"] == 1500.0

    def test_no_restrictions_for_model(self, rbac: RBACManager) -> None:
        values = {"name": "Test", "code": "ABC"}
        result = rbac.sanitize_write_values(values, "product.category", [], False)
        assert result == {"name": "Test", "code": "ABC"}

    def test_original_values_not_mutated(self, rbac: RBACManager) -> None:
        values = {"wage": 5000, "name": "Test"}
        rbac.sanitize_write_values(values, "hr.employee", ["base.group_user"], False)
        assert values["wage"] == 5000


# ── get_visible_fields ─────────────────────────────────────────────


class TestGetVisibleFields:
    def test_admin_all_visible(self, rbac: RBACManager) -> None:
        result = rbac.get_visible_fields("hr.employee", [], True)
        assert result is None

    def test_non_admin_restricted_model(self, rbac: RBACManager) -> None:
        result = rbac.get_visible_fields("hr.employee", [], False)
        assert result is not None
        assert "wage" in result
        assert "salary" in result

    def test_non_admin_unrestricted_model(self, rbac: RBACManager) -> None:
        result = rbac.get_visible_fields("product.category", [], False)
        assert result is None

    def test_user_with_some_groups(self, rbac: RBACManager) -> None:
        result = rbac.get_visible_fields("hr.employee", ["hr.group_hr_manager"], False)
        # model_access sensitive_fields still applies regardless of group
        assert result is not None
        assert "wage" in result
