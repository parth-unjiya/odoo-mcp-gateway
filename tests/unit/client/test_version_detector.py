"""Tests for the version detector."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from odoo_mcp_gateway.client.base import OdooClientBase
from odoo_mcp_gateway.client.exceptions import OdooVersionError
from odoo_mcp_gateway.core.version.detector import (
    VersionInfo,
    detect_version,
)


def _mock_client(
    version_info: dict[str, Any],
) -> OdooClientBase:
    client = AsyncMock(spec=OdooClientBase)
    client.get_version = AsyncMock(return_value=version_info)
    return client


class TestDetectVersion:
    async def test_parse_17_community(self) -> None:
        client = _mock_client({"server_version": "17.0"})
        info = await detect_version(client)
        assert info.major == 17
        assert info.minor == 0
        assert info.edition == "community"
        assert info.full_string == "17.0"

    async def test_parse_18_community(self) -> None:
        client = _mock_client({"server_version": "18.0"})
        info = await detect_version(client)
        assert info.major == 18
        assert info.minor == 0
        assert info.edition == "community"

    async def test_parse_19_enterprise(self) -> None:
        client = _mock_client({"server_version": "19.0+e"})
        info = await detect_version(client)
        assert info.major == 19
        assert info.minor == 0
        assert info.edition == "enterprise"

    async def test_parse_saas_18_1(self) -> None:
        client = _mock_client({"server_version": "saas~18.1"})
        info = await detect_version(client)
        assert info.major == 18
        assert info.minor == 1
        assert info.edition == "community"

    async def test_parse_saas_enterprise(self) -> None:
        client = _mock_client({"server_version": "saas~19.1+e"})
        info = await detect_version(client)
        assert info.major == 19
        assert info.edition == "enterprise"

    async def test_micro_from_version_info(self) -> None:
        client = _mock_client(
            {
                "server_version": "17.0",
                "server_version_info": [17, 0, 3, "final", 0],
            }
        )
        info = await detect_version(client)
        assert info.micro == 3

    async def test_micro_defaults_to_zero(self) -> None:
        client = _mock_client({"server_version": "17.0"})
        info = await detect_version(client)
        assert info.micro == 0

    async def test_unsupported_version_raises(self) -> None:
        client = _mock_client({"server_version": "16.0"})
        with pytest.raises(OdooVersionError, match="not supported"):
            await detect_version(client)

    async def test_unsupported_version_14(self) -> None:
        client = _mock_client({"server_version": "14.0"})
        with pytest.raises(OdooVersionError):
            await detect_version(client)

    async def test_empty_version_raises(self) -> None:
        client = _mock_client({"server_version": ""})
        with pytest.raises(OdooVersionError, match="did not report"):
            await detect_version(client)

    async def test_missing_version_key_raises(self) -> None:
        client = _mock_client({})
        with pytest.raises(OdooVersionError, match="did not report"):
            await detect_version(client)

    async def test_garbage_version_raises(self) -> None:
        client = _mock_client({"server_version": "not-a-version"})
        with pytest.raises(OdooVersionError, match="Cannot parse"):
            await detect_version(client)

    async def test_full_string_preserved(self) -> None:
        client = _mock_client({"server_version": "18.0+e"})
        info = await detect_version(client)
        assert info.full_string == "18.0+e"

    async def test_version_info_dataclass(self) -> None:
        info = VersionInfo(
            major=17,
            minor=0,
            micro=0,
            edition="community",
            full_string="17.0",
        )
        assert info.major == 17
        assert info.edition == "community"
