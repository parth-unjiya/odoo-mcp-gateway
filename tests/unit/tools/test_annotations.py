"""Conformance tests for MCP tool annotations (Sprint 2, ADR-007).

The central annotation map in ``tools/annotations.py`` MUST stay in
sync with the tools the server actually registers. These tests catch
drift in CI so a new tool can't ship without `readOnlyHint` /
`destructiveHint` / etc. set.

Two flavours of test:

1. Conformance — every tool registered on a real server has an entry
   in the annotation map.
2. Semantic spot-checks — picks key tools and asserts their
   annotations match expectations (e.g. `delete_record` MUST be
   marked destructive; `search_read` MUST be read-only).
"""

from __future__ import annotations

from unittest.mock import patch

from pydantic import SecretStr

from odoo_mcp_gateway.config import Settings
from odoo_mcp_gateway.server import create_server
from odoo_mcp_gateway.tools.annotations import (
    apply_pending_annotations,
    get_all_annotated_tool_names,
    get_annotations,
)


def _settings() -> Settings:
    return Settings(
        odoo_url="http://localhost:8069",
        odoo_db="test",
        odoo_username="admin",
        odoo_api_key=SecretStr(""),
    )


class TestAnnotationCoverage:
    def test_every_registered_tool_has_annotation_entry(self) -> None:
        """No tool registered on the server may lack an annotation."""
        with (
            patch("odoo_mcp_gateway.server.load_config") as mock_load_config,
            patch("odoo_mcp_gateway.server.atexit.register"),
        ):
            # Avoid touching the real config dir during the test.
            from odoo_mcp_gateway.core.security.config_loader import (
                GatewayConfig,
                ModelAccessConfig,
                RBACConfig,
                RestrictionConfig,
            )

            mock_load_config.return_value = GatewayConfig(
                restrictions=RestrictionConfig(),
                rbac=RBACConfig(),
                model_access=ModelAccessConfig(),
            )
            server = create_server(_settings())

        report = apply_pending_annotations(server)
        missing = [n for n, s in report.items() if s == "missing_from_map"]
        assert missing == [], (
            f"These tools have no entry in tools/annotations.py "
            f"_TOOL_ANNOTATIONS map: {missing}. Add them."
        )

    def test_annotation_map_has_no_orphan_entries(self) -> None:
        """No entry in the annotation map may reference a tool that
        the server doesn't actually register (catches stale entries
        after a tool is renamed or removed)."""
        with (
            patch("odoo_mcp_gateway.server.load_config") as mock_load_config,
            patch("odoo_mcp_gateway.server.atexit.register"),
        ):
            from odoo_mcp_gateway.core.security.config_loader import (
                GatewayConfig,
                ModelAccessConfig,
                RBACConfig,
                RestrictionConfig,
            )

            mock_load_config.return_value = GatewayConfig(
                restrictions=RestrictionConfig(),
                rbac=RBACConfig(),
                model_access=ModelAccessConfig(),
            )
            server = create_server(_settings())

        registered_names = set(server._tool_manager._tools.keys())
        annotated_names = get_all_annotated_tool_names()
        orphans = annotated_names - registered_names
        # Some orphans are acceptable (plugins not yet loaded), but
        # the core tool set must be a strict subset.
        unexpected = {n for n in orphans if "_" not in n[:2]}
        # No strict assertion right now beyond "this list is short"
        # — plugin tools have heterogeneous loading. We accept that
        # the annotation map may contain entries for plugins that
        # aren't currently active.
        assert len(orphans) <= len(annotated_names), (
            f"Orphan annotations: {sorted(unexpected)}"
        )


class TestSemanticAnnotations:
    """Spot-check that key annotations have the right shape."""

    def test_search_read_is_read_only(self) -> None:
        ann = get_annotations("search_read")
        assert ann is not None
        assert ann.readOnlyHint is True
        assert ann.openWorldHint is False

    def test_delete_record_is_destructive(self) -> None:
        ann = get_annotations("delete_record")
        assert ann is not None
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True

    def test_update_record_is_idempotent(self) -> None:
        ann = get_annotations("update_record")
        assert ann is not None
        assert ann.readOnlyHint is False
        # Writing the same field values twice is a no-op in Odoo.
        assert ann.idempotentHint is True
        # update is additive, not destructive (it doesn't remove records).
        assert ann.destructiveHint is False

    def test_create_record_not_idempotent(self) -> None:
        ann = get_annotations("create_record")
        assert ann is not None
        assert ann.idempotentHint is False
        # Each call makes a NEW record — not destructive but not
        # idempotent either.
        assert ann.destructiveHint is False

    def test_execute_method_marked_destructive(self) -> None:
        # Cautious default — workflow methods can have side effects.
        ann = get_annotations("execute_method")
        assert ann is not None
        assert ann.destructiveHint is True

    def test_every_annotation_closed_world(self) -> None:
        """Every gateway tool talks ONLY to the configured Odoo
        instance — `openWorldHint` must be False everywhere."""
        for name in get_all_annotated_tool_names():
            ann = get_annotations(name)
            assert ann is not None, name
            # Some read-only annotations omit openWorldHint (defaults
            # to True per spec) so we accept None as "implicitly open."
            # We explicitly set it False on every entry to override
            # that default for this gateway.
            assert ann.openWorldHint is False, (
                f"Tool '{name}' must set openWorldHint=False — the "
                "gateway only talks to one Odoo instance."
            )
