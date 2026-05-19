"""Regression tests for the formatter XSS hardening (P2-14).

The MCP gateway returns JSON, not HTML — so raw ``<script>`` tags in
record fields are not dangerous on the wire. Defence-in-depth: when
``format_records`` renders a markdown table that a downstream chat
client MAY render as HTML, we escape angle brackets only when a known
dangerous pattern (script/iframe/onhandler/etc.) is detected. Benign
content (URLs with `&`, accented characters) passes through unchanged.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from odoo_mcp_gateway.utils.formatting import format_records


class TestXssEscaping:
    def test_script_tag_is_escaped(self) -> None:
        records = [{"id": 1, "name": "<script>alert(1)</script>"}]
        out = format_records(records, model="res.partner")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_iframe_tag_is_escaped(self) -> None:
        records = [{"id": 1, "name": "<iframe src='evil'></iframe>"}]
        out = format_records(records, model="res.partner")
        assert "<iframe" not in out

    def test_onclick_handler_is_escaped(self) -> None:
        records = [{"id": 1, "name": "<img src=x onerror=alert(1)>"}]
        out = format_records(records, model="res.partner")
        assert "<img" not in out

    def test_benign_content_passes_through(self) -> None:
        records = [{"id": 1, "name": "Smith & Sons (Pvt) Ltd."}]
        out = format_records(records, model="res.partner")
        # Ampersand stays unescaped — entities alone cannot execute.
        assert "&" in out
        assert "&amp;" not in out

    def test_url_with_ampersand_unchanged(self) -> None:
        records = [{"id": 1, "name": "Partner", "website": "https://e.com/?a=1&b=2"}]
        out = format_records(records, model="res.partner")
        assert "https://e.com/?a=1&b=2" in out

    def test_inline_lt_gt_in_comparison_unchanged(self) -> None:
        # "x > 5" is common math notation in note/description fields.
        # Since there's no markup keyword, we leave it as-is.
        records = [{"id": 1, "name": "x > 5 and y < 10"}]
        out = format_records(records, model="res.partner")
        # Note: this single field has no dangerous markers, so the
        # bare > < survives.
        assert ">" in out and "<" in out
