"""Abstract base class for Odoo RPC clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class Credential:
    """Wrapper that prevents credential leakage via repr/str.

    Stores the actual credential in a private attribute and overrides
    __repr__/__str__ to never show the value. Provides explicit
    .reveal() to access the cleartext when needed.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str | None) -> None:
        self._value: str | None = value

    def reveal(self) -> str | None:
        """Return the cleartext credential. Use sparingly."""
        return self._value

    def is_set(self) -> bool:
        """Check if a credential is stored."""
        return self._value is not None and self._value != ""

    def clear(self) -> None:
        """Zero out the stored credential."""
        self._value = None

    def __repr__(self) -> str:
        return f"Credential({'***' if self.is_set() else 'unset'})"

    def __str__(self) -> str:
        return "***"

    def __bool__(self) -> bool:
        return self.is_set()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Credential):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        # Credentials are mutable (clear() can be called), but defining
        # __eq__ removes the default __hash__. Hash by identity since
        # using the cleartext for hashing would defeat the purpose.
        return id(self)


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
