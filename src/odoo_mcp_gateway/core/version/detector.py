"""Odoo version detection from the ``/xmlrpc/2/common`` version endpoint."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from odoo_mcp_gateway.client.base import OdooClientBase
from odoo_mcp_gateway.client.exceptions import OdooVersionError

_SUPPORTED_MAJORS = {17, 18, 19}

# Matches patterns like "17.0", "18.0+e", "saas~18.1"
_VERSION_RE = re.compile(r"(?:saas~)?(\d+)\.(\d+)(\+e)?")


@dataclass
class VersionInfo:
    """Parsed Odoo version information."""

    major: int
    minor: int
    micro: int
    edition: str  # "community" or "enterprise"
    full_string: str


async def detect_version(client: OdooClientBase) -> VersionInfo:
    """Call ``get_version()`` and parse the result into a :class:`VersionInfo`.

    Raises :class:`OdooVersionError` when the version cannot be parsed or
    the major version is not in the supported set (17, 18, 19).
    """
    info: dict[str, Any] = await client.get_version()

    raw: str = str(info.get("server_version", ""))
    if not raw:
        raise OdooVersionError("Odoo did not report a server_version")

    match = _VERSION_RE.search(raw)
    if match is None:
        raise OdooVersionError(f"Cannot parse Odoo version string: {raw!r}")

    major = int(match.group(1))
    minor = int(match.group(2))
    enterprise = match.group(3) is not None  # "+e"

    if major not in _SUPPORTED_MAJORS:
        raise OdooVersionError(
            f"Odoo {major} is not supported (supported: "
            f"{', '.join(str(v) for v in sorted(_SUPPORTED_MAJORS))})"
        )

    # Micro version may be in server_version_info list if present.
    ver_info_list = info.get("server_version_info")
    micro = 0
    if isinstance(ver_info_list, (list, tuple)) and len(ver_info_list) >= 3:
        try:
            micro = int(ver_info_list[2])
        except (ValueError, TypeError):
            micro = 0

    edition = "enterprise" if enterprise else "community"

    return VersionInfo(
        major=major,
        minor=minor,
        micro=micro,
        edition=edition,
        full_string=raw,
    )
