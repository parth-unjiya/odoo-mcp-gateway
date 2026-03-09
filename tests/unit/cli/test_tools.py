"""Tests for CLI utility tools."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import httpx

from odoo_mcp_gateway.cli.tools import (
    _list_models,
    _test_connection,
    _validate_config,
    main,
)

# ------------------------------------------------------------------
# test-connection
# ------------------------------------------------------------------


class TestTestConnection:
    def test_success(self) -> None:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200

        with patch("odoo_mcp_gateway.cli.tools.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_resp
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            args = argparse.Namespace(url="http://localhost:8069", timeout=10.0)
            result = _test_connection(args)

        assert result == 0
        mock_httpx.post.assert_called_once()

    def test_connect_error(self) -> None:
        with patch("odoo_mcp_gateway.cli.tools.httpx") as mock_httpx:
            mock_httpx.post.side_effect = httpx.ConnectError("refused")
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            args = argparse.Namespace(url="http://localhost:8069", timeout=10.0)
            result = _test_connection(args)

        assert result == 1

    def test_timeout(self) -> None:
        with patch("odoo_mcp_gateway.cli.tools.httpx") as mock_httpx:
            mock_httpx.post.side_effect = httpx.TimeoutException("slow")
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            args = argparse.Namespace(url="http://localhost:8069", timeout=5.0)
            result = _test_connection(args)

        assert result == 1

    def test_non_200_response(self) -> None:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 503

        with patch("odoo_mcp_gateway.cli.tools.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_resp
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            args = argparse.Namespace(url="http://localhost:8069", timeout=10.0)
            result = _test_connection(args)

        assert result == 1

    def test_url_trailing_slash_stripped(self) -> None:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200

        with patch("odoo_mcp_gateway.cli.tools.httpx") as mock_httpx:
            mock_httpx.post.return_value = mock_resp
            mock_httpx.ConnectError = httpx.ConnectError
            mock_httpx.TimeoutException = httpx.TimeoutException
            args = argparse.Namespace(url="http://localhost:8069/", timeout=10.0)
            _test_connection(args)

        call_url = mock_httpx.post.call_args[0][0]
        assert not call_url.startswith("http://localhost:8069//")


# ------------------------------------------------------------------
# validate-config
# ------------------------------------------------------------------


class TestValidateConfig:
    @patch("odoo_mcp_gateway.cli.tools.load_config")
    def test_valid(self, mock_load: MagicMock) -> None:
        mock_cfg = MagicMock()
        mock_cfg.restrictions.always_blocked = ["a", "b"]
        mock_cfg.restrictions.admin_only = ["c"]
        mock_cfg.restrictions.blocked_methods = ["d"]
        mock_cfg.model_access.default_policy = "deny"
        mock_cfg.model_access.stock_models = {"full_crud": ["sale.order"]}
        mock_cfg.model_access.custom_models = {"full_crud": []}
        mock_load.return_value = mock_cfg
        args = argparse.Namespace(config_dir="config")
        result = _validate_config(args)
        assert result == 0

    @patch("odoo_mcp_gateway.cli.tools.load_config")
    def test_invalid(self, mock_load: MagicMock) -> None:
        mock_load.side_effect = ValueError("Bad config")
        args = argparse.Namespace(config_dir="bad")
        result = _validate_config(args)
        assert result == 1


# ------------------------------------------------------------------
# list-models
# ------------------------------------------------------------------


class TestListModels:
    @patch("odoo_mcp_gateway.cli.tools.RestrictionChecker")
    @patch("odoo_mcp_gateway.cli.tools.load_config")
    def test_lists_models(
        self,
        mock_load: MagicMock,
        mock_checker_cls: MagicMock,
    ) -> None:
        mock_cfg = MagicMock()
        mock_load.return_value = mock_cfg

        mock_checker = MagicMock()
        mock_checker.get_accessible_models.side_effect = [
            ["sale.order", "res.partner", "ir.model"],
            ["sale.order", "res.partner"],
        ]
        mock_checker_cls.return_value = mock_checker
        args = argparse.Namespace(config_dir="config")
        result = _list_models(args)
        assert result == 0

    @patch("odoo_mcp_gateway.cli.tools.load_config")
    def test_config_error(self, mock_load: MagicMock) -> None:
        mock_load.side_effect = ValueError("Bad")
        args = argparse.Namespace(config_dir="bad")
        result = _list_models(args)
        assert result == 1


# ------------------------------------------------------------------
# main() dispatch
# ------------------------------------------------------------------


class TestMain:
    def test_no_command_shows_help(self, capsys: object) -> None:
        with patch("sys.argv", ["odoo-mcp-tools"]):
            main()  # Should return without error

    @patch("odoo_mcp_gateway.cli.tools._test_connection")
    def test_dispatches_test_connection(self, mock_tc: MagicMock) -> None:
        mock_tc.return_value = 0
        with patch(
            "sys.argv",
            [
                "odoo-mcp-tools",
                "test-connection",
                "--url",
                "http://localhost:8069",
            ],
        ):
            with patch("sys.exit") as mock_exit:
                main()
                mock_exit.assert_called_once_with(0)
        mock_tc.assert_called_once()

    @patch("odoo_mcp_gateway.cli.tools._validate_config")
    def test_dispatches_validate_config(self, mock_vc: MagicMock) -> None:
        mock_vc.return_value = 0
        with patch(
            "sys.argv",
            [
                "odoo-mcp-tools",
                "validate-config",
                "--config-dir",
                "config",
            ],
        ):
            with patch("sys.exit") as mock_exit:
                main()
                mock_exit.assert_called_once_with(0)
        mock_vc.assert_called_once()

    @patch("odoo_mcp_gateway.cli.tools._list_models")
    def test_dispatches_list_models(self, mock_lm: MagicMock) -> None:
        mock_lm.return_value = 0
        with patch(
            "sys.argv",
            [
                "odoo-mcp-tools",
                "list-models",
                "--config-dir",
                "config",
            ],
        ):
            with patch("sys.exit") as mock_exit:
                main()
                mock_exit.assert_called_once_with(0)
        mock_lm.assert_called_once()
