"""Abstract base class for Odoo RPC clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AuthResult:
    """Stores the result of an Odoo authentication call."""

    uid: int
    session_id: str | None
    user_context: dict[str, Any]
    is_admin: bool
    groups: list[str]
    username: str
    database: str


class OdooClientBase(ABC):
    """Protocol that both JSON-RPC and XML-RPC clients implement."""

    @abstractmethod
    async def authenticate(self, db: str, login: str, password: str) -> AuthResult: ...

    @abstractmethod
    async def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any: ...

    @abstractmethod
    async def get_version(self) -> dict[str, Any]: ...

    @abstractmethod
    async def close(self) -> None: ...
