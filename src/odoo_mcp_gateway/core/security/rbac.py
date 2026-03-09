"""RBAC manager: role-based access control overlays."""

from __future__ import annotations

from typing import Any

from .config_loader import ModelAccessConfig, RBACConfig

_REDACTED = "***"


class RBACManager:
    """Enforces role-based access control based on Odoo group memberships.

    Handles tool-level access, sensitive field redaction, and field-level
    write restrictions.
    """

    def __init__(self, config: RBACConfig, model_access: ModelAccessConfig) -> None:
        self._tool_group_requirements = config.tool_group_requirements
        self._sensitive_fields = config.sensitive_fields
        self._field_group_overrides = config.field_group_overrides
        self._model_access_sensitive = model_access.sensitive_fields

    def check_tool_access(
        self,
        tool_name: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> str | None:
        """Check if user has required groups for the tool.

        Returns error message if denied, None if allowed.
        Admin bypasses all tool group requirements.
        """
        if is_admin:
            return None

        if tool_name not in self._tool_group_requirements:
            return None

        required = self._tool_group_requirements[tool_name]
        user_set = set(user_groups)

        if not any(g in user_set for g in required):
            return f"Tool '{tool_name}' requires one of: {', '.join(required)}"

        return None

    def filter_response_fields(
        self,
        records: list[dict[str, Any]],
        model: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> list[dict[str, Any]]:
        """Replace sensitive field values with '***' where user lacks group.

        Returns a new list of records with redacted values.
        Admin sees all fields unredacted.
        """
        if is_admin:
            return records

        redact_fields = self._get_redact_fields(model, user_groups)

        if not redact_fields:
            return records

        result = []
        for record in records:
            filtered = dict(record)
            for field_name in redact_fields:
                if field_name in filtered:
                    filtered[field_name] = _REDACTED
            result.append(filtered)

        return result

    def sanitize_write_values(
        self,
        values: dict[str, Any],
        model: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> dict[str, Any]:
        """Remove fields the user cannot write from the values dict.

        Returns a new dict with disallowed fields removed.
        Admin can write all fields.
        """
        if is_admin:
            return dict(values)

        blocked = self._get_write_blocked_fields(model, user_groups)

        if not blocked:
            return dict(values)

        return {k: v for k, v in values.items() if k not in blocked}

    def get_visible_fields(
        self,
        model: str,
        user_groups: list[str],
        is_admin: bool,
    ) -> set[str] | None:
        """Return set of field names that should be redacted/hidden.

        Returns None if all fields are visible.

        If there are no restrictions for this model, returns None (all visible).
        Admin always sees all fields (returns None).
        When a non-None set is returned, the caller should exclude or redact
        these fields from the response.
        """
        if is_admin:
            return None

        redact_fields = self._get_redact_fields(model, user_groups)

        if not redact_fields:
            return None

        return redact_fields

    def _get_redact_fields(self, model: str, user_groups: list[str]) -> set[str]:
        """Get set of fields that should be redacted for this user/model."""
        redact: set[str] = set()
        user_set = set(user_groups)

        # Check RBAC sensitive_fields
        if model in self._sensitive_fields:
            model_config = self._sensitive_fields[model]
            if isinstance(model_config, dict):
                required_group = model_config.get("required_group", "")
                if required_group and required_group not in user_set:
                    fields = model_config.get("fields", [])
                    redact.update(fields)

        # Check model_access sensitive_fields
        if model in self._model_access_sensitive:
            redact.update(self._model_access_sensitive[model])

        # Check field_group_overrides for read access
        if model in self._field_group_overrides:
            for field_name, overrides in self._field_group_overrides[model].items():
                if isinstance(overrides, dict):
                    read_group = overrides.get("read", "")
                    if read_group and read_group not in user_set:
                        redact.add(field_name)

        return redact

    def _get_write_blocked_fields(self, model: str, user_groups: list[str]) -> set[str]:
        """Get set of fields the user cannot write for this model."""
        blocked: set[str] = set()
        user_set = set(user_groups)

        # Check RBAC sensitive_fields
        if model in self._sensitive_fields:
            model_config = self._sensitive_fields[model]
            if isinstance(model_config, dict):
                required_group = model_config.get("required_group", "")
                if required_group and required_group not in user_set:
                    fields = model_config.get("fields", [])
                    blocked.update(fields)

        # Check field_group_overrides for write access
        if model in self._field_group_overrides:
            for field_name, overrides in self._field_group_overrides[model].items():
                if isinstance(overrides, dict):
                    write_group = overrides.get("write", "")
                    if write_group and write_group not in user_set:
                        blocked.add(field_name)

        return blocked
