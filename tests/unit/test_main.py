"""Tests for __main__.py entry point."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

# ── main() with stdio transport ──────────────────────────────────


class TestMainStdio:
    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    def test_main_stdio_transport(self, mock_settings, mock_create_server):
        """main() with default stdio transport calls server.run(transport='stdio')."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings

        mock_server = MagicMock()
        mock_create_server.return_value = mock_server

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_settings.assert_called_once()
        mock_create_server.assert_called_once_with(settings)
        mock_server.run.assert_called_once_with(transport="stdio")

    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    def test_main_stdio_is_default(self, mock_settings, mock_create_server):
        """When mcp_transport is not 'streamable-http', stdio is used."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "DEBUG"
        mock_settings.return_value = settings

        mock_server = MagicMock()
        mock_create_server.return_value = mock_server

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_server.run.assert_called_once_with(transport="stdio")


# ── main() with streamable-http transport ────────────────────────


class TestMainStreamableHTTP:
    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    def test_main_streamable_http_transport(
        self, mock_settings, mock_create_server
    ):
        """main() with streamable-http transport uses that transport."""
        settings = MagicMock()
        settings.mcp_transport = "streamable-http"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings

        mock_server = MagicMock()
        mock_create_server.return_value = mock_server

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_create_server.assert_called_once_with(settings)
        mock_server.run.assert_called_once_with(transport="streamable-http")


# ── Logging setup ────────────────────────────────────────────────


class TestLoggingSetup:
    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    @patch("odoo_mcp_gateway.__main__.logging.basicConfig")
    def test_logging_configured_with_info_level(
        self, mock_basic_config, mock_settings, mock_create_server
    ):
        """main() sets up logging with the level from settings."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings
        mock_create_server.return_value = MagicMock()

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_basic_config.assert_called_once()
        call_kwargs = mock_basic_config.call_args
        assert call_kwargs[1]["level"] == logging.INFO

    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    @patch("odoo_mcp_gateway.__main__.logging.basicConfig")
    def test_logging_configured_with_debug_level(
        self, mock_basic_config, mock_settings, mock_create_server
    ):
        """main() respects DEBUG log level setting."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "DEBUG"
        mock_settings.return_value = settings
        mock_create_server.return_value = MagicMock()

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_basic_config.assert_called_once()
        call_kwargs = mock_basic_config.call_args
        assert call_kwargs[1]["level"] == logging.DEBUG

    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    @patch("odoo_mcp_gateway.__main__.logging.basicConfig")
    def test_logging_configured_with_warning_level(
        self, mock_basic_config, mock_settings, mock_create_server
    ):
        """main() respects WARNING log level setting."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "WARNING"
        mock_settings.return_value = settings
        mock_create_server.return_value = MagicMock()

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_basic_config.assert_called_once()
        call_kwargs = mock_basic_config.call_args
        assert call_kwargs[1]["level"] == logging.WARNING

    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    @patch("odoo_mcp_gateway.__main__.logging.basicConfig")
    def test_logging_format_includes_timestamp_and_level(
        self, mock_basic_config, mock_settings, mock_create_server
    ):
        """main() sets the expected log format with timestamp and level."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings
        mock_create_server.return_value = MagicMock()

        from odoo_mcp_gateway.__main__ import main

        main()

        call_kwargs = mock_basic_config.call_args
        fmt = call_kwargs[1]["format"]
        assert "%(asctime)s" in fmt
        assert "%(levelname)s" in fmt
        assert "%(name)s" in fmt


# ── Logger info message ──────────────────────────────────────────


class TestLoggerMessages:
    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    @patch("odoo_mcp_gateway.__main__.logger")
    def test_startup_message_logged(
        self, mock_logger, mock_settings, mock_create_server
    ):
        """main() logs a startup message with version and transport."""
        settings = MagicMock()
        settings.mcp_transport = "stdio"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings
        mock_create_server.return_value = MagicMock()

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_logger.info.assert_called_once()
        log_args = mock_logger.info.call_args
        assert "transport" in log_args[0][0].lower() or (
            "transport" in str(log_args)
        )

    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    @patch("odoo_mcp_gateway.__main__.logger")
    def test_startup_message_includes_transport_value(
        self, mock_logger, mock_settings, mock_create_server
    ):
        """main() passes the actual transport value to the logger."""
        settings = MagicMock()
        settings.mcp_transport = "streamable-http"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings
        mock_create_server.return_value = MagicMock()

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0]
        assert "streamable-http" in call_args


# ── Edge case: non-standard transport string ─────────────────────


class TestTransportEdgeCases:
    @patch("odoo_mcp_gateway.__main__.create_server")
    @patch("odoo_mcp_gateway.__main__.Settings")
    def test_unknown_transport_falls_back_to_stdio(
        self, mock_settings, mock_create_server
    ):
        """Non-standard transport falls back to stdio."""
        settings = MagicMock()
        settings.mcp_transport = "something-else"
        settings.mcp_log_level = "INFO"
        mock_settings.return_value = settings

        mock_server = MagicMock()
        mock_create_server.return_value = mock_server

        from odoo_mcp_gateway.__main__ import main

        main()

        mock_server.run.assert_called_once_with(transport="stdio")
