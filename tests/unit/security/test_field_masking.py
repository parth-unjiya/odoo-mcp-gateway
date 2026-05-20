"""UAT L2 (Odoo 18) — document intentional VAT masking for non-admin users.

The example ``model_access.yaml`` lists ``vat`` (and ``bank_ids``,
``credit_limit``) under ``res.partner.sensitive_fields``. The RBAC
manager redacts those fields to ``"***"`` in responses for any caller
who is not an admin — INCLUDING a portal user reading their own
contact record. That is the INTENDED behaviour: the gateway treats
``vat`` as PII regardless of record ownership, on the grounds that
even self-reads through an LLM agent can leak the field to a
downstream context the user did not anticipate.

These tests pin the intentional behaviour so a future change to the
masking rule has to break a documented expectation rather than slip
through unnoticed. If product decides VAT should be self-readable in
the future, this file is the canary that needs editing first.
"""

from __future__ import annotations

from odoo_mcp_gateway.core.security.config_loader import (
    ModelAccessConfig,
    RBACConfig,
)
from odoo_mcp_gateway.core.security.rbac import RBACManager


def _rbac() -> RBACManager:
    """Build an RBAC manager mirroring the example YAML's vat masking."""
    model_access = ModelAccessConfig(
        sensitive_fields={
            "res.partner": ["vat", "bank_ids", "credit_limit"],
        },
    )
    return RBACManager(config=RBACConfig(), model_access=model_access)


class TestVatMaskingIntentional:
    def test_admin_sees_vat_unmasked(self) -> None:
        rbac = _rbac()
        records = [{"id": 1, "name": "Alice", "vat": "DE123456789"}]
        out = rbac.filter_response_fields(
            records,
            "res.partner",
            user_groups=[],
            is_admin=True,
        )
        assert out[0]["vat"] == "DE123456789"

    def test_internal_user_sees_vat_masked(self) -> None:
        """Internal demo user reading any partner gets ``vat: "***"``."""
        rbac = _rbac()
        records = [{"id": 1, "name": "Alice", "vat": "DE123456789"}]
        out = rbac.filter_response_fields(
            records,
            "res.partner",
            user_groups=["base.group_user"],
            is_admin=False,
        )
        assert out[0]["vat"] == "***"

    def test_portal_user_sees_vat_masked_even_for_self(self) -> None:
        """Portal user reading own contact gets ``vat: "***"`` — by design.

        UAT L2 confirmed that masking applies regardless of record
        ownership. The model-access ``sensitive_fields`` list is the
        sole policy here; the RBAC layer does not exempt self-reads.
        """
        rbac = _rbac()
        records = [{"id": 7, "name": "Portal Bob", "vat": "DE999888777"}]
        out = rbac.filter_response_fields(
            records,
            "res.partner",
            user_groups=[],
            is_admin=False,
        )
        assert out[0]["vat"] == "***"
        # Other masked fields (per example YAML) also redacted.
        # We only included ``vat`` in this record; just ensure non-VAT
        # fields round-trip unchanged.
        assert out[0]["name"] == "Portal Bob"

    def test_non_sensitive_field_not_masked(self) -> None:
        rbac = _rbac()
        records = [{"id": 1, "name": "Alice", "phone": "+49 123"}]
        out = rbac.filter_response_fields(
            records,
            "res.partner",
            user_groups=[],
            is_admin=False,
        )
        # ``name`` and ``phone`` are NOT in the sensitive list.
        assert out[0]["name"] == "Alice"
        assert out[0]["phone"] == "+49 123"
