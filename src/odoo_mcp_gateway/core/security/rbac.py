"""RBAC manager: role-based access control overlays."""

from __future__ import annotations

from typing import Any, Literal, overload

from odoo_mcp_gateway.client.base import AuthResult

from .config_loader import ModelAccessConfig, RBACConfig

_REDACTED = "***"

# Portal-typed group XML IDs we recognise. A user whose group set is
# empty OR contains only entries from this set is classified as a
# portal user. The list intentionally stays tight — only the canonical
# Odoo portal groups — so we err on the side of NOT misclassifying
# internal users with sparse group memberships as portal.
_PORTAL_GROUP_XML_IDS: frozenset[str] = frozenset(
    {
        "base.group_portal",
        "base.group_public",
    }
)


def is_portal_user(auth_result: AuthResult | None) -> bool:
    """Return ``True`` if the caller is a portal/external user.

    Detection rule (defence-in-depth — UPDATED for UAT v0.3.3 MED):

    1. Admins are never portal — short-circuit on ``is_admin``.
    2. Consult ``group_xml_ids`` first (stable across Odoo versions
       and translations). When non-empty:
       * If it contains ONLY recognised portal-typed group IDs
         (``base.group_portal``, ``base.group_public``) → portal.
       * If it contains any internal group → NOT portal.
    3. When ``group_xml_ids`` is empty, fall back to the
       locale-dependent ``groups`` field with the same rule. The
       fallback REQUIRES at least one explicit portal-typed entry —
       merely-empty / unmappable group lists no longer collapse to
       "portal". (Earlier behaviour misclassified internal users
       whose ``_fetch_groups`` failed silently during login as
       portal, producing the puzzling
       ``"Profile not available for portal users"`` error reported
       in UAT v0.3.3 for a non-portal helpdesk_manager.)
    4. BOTH collections empty → return ``False`` (cannot prove
       portal membership; fail OPEN to the internal-user path so
       legitimate users see the canonical
       ``"No employee profile found"`` / ACL response instead of
       a misleading portal hint).

    Returns ``False`` for unauthenticated callers — they have no
    authorisation surface at all, so RBAC tiering doesn't apply.
    """
    if auth_result is None:
        return False
    if auth_result.is_admin:
        return False
    xml_ids = list(auth_result.group_xml_ids or [])
    if xml_ids:
        # Strict subset: every entry must be a known portal group.
        return all(g in _PORTAL_GROUP_XML_IDS for g in xml_ids)
    # Fall back to ``groups`` (display names) when XML IDs aren't set.
    groups = list(auth_result.groups or [])
    if groups:
        # Display names like "base.group_user" sometimes appear in
        # ``groups`` too (auth_manager fills both for newer clients).
        # We accept either spelling here for robustness — BUT we now
        # also require at least one entry to be a recognised portal
        # group. A wholly-unmappable group list (all display names,
        # none in the portal set) no longer flips to "portal".
        if not any(g in _PORTAL_GROUP_XML_IDS for g in groups):
            return False
        return all(g in _PORTAL_GROUP_XML_IDS for g in groups)
    # BOTH empty → genuine portal/anonymous-ish user CANNOT be proven.
    # Previously returned True; that misclassified internal users whose
    # group fetch failed silently. Return False so the calling code
    # falls through to the no-employee-found / standard ACL path.
    return False


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

        TODO: This only redacts top-level fields. Nested records returned by
        Odoo's `web_read` (or recursive `read` with relational expansions) can
        slip through unredacted. Since `web_read` isn't currently exposed via
        the gateway tools, this is acceptable, but if/when it is, the
        redaction needs to recurse into nested dicts/lists keyed by relational
        field metadata. Track this when adding `web_read` support.
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

    @overload
    def sanitize_write_values(
        self,
        values: dict[str, Any],
        model: str,
        user_groups: list[str],
        is_admin: bool,
        *,
        return_dropped: Literal[False] = False,
    ) -> dict[str, Any]: ...

    @overload
    def sanitize_write_values(
        self,
        values: dict[str, Any],
        model: str,
        user_groups: list[str],
        is_admin: bool,
        *,
        return_dropped: Literal[True],
    ) -> tuple[dict[str, Any], list[str]]: ...

    def sanitize_write_values(
        self,
        values: dict[str, Any],
        model: str,
        user_groups: list[str],
        is_admin: bool,
        *,
        return_dropped: bool = False,
    ) -> dict[str, Any] | tuple[dict[str, Any], list[str]]:
        """Remove fields the user cannot write from the values dict.

        Returns a new dict with disallowed fields removed.
        Admin can write all fields.

        When ``return_dropped`` is True, returns a tuple
        ``(sanitized_dict, dropped_field_names)`` so callers can detect
        silent drops (e.g. a user setting ``user_id`` on a helpdesk ticket
        without the necessary group). By default the method still returns
        just the sanitized dict for backward compatibility.
        """
        if is_admin:
            sanitized = dict(values)
            if return_dropped:
                return sanitized, []
            return sanitized

        blocked = self._get_write_blocked_fields(model, user_groups)

        if not blocked:
            sanitized = dict(values)
            if return_dropped:
                return sanitized, []
            return sanitized

        sanitized = {k: v for k, v in values.items() if k not in blocked}

        if return_dropped:
            # Preserve original key order from the input for deterministic output.
            dropped = [k for k in values if k in blocked]
            return sanitized, dropped

        return sanitized

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
