"""Error sanitizer: cleans internal details from error messages."""

from __future__ import annotations

import re


class ErrorSanitizer:
    """Strips internal details from error messages before exposing to clients.

    Removes file paths, SQL queries, tracebacks, and database names.
    Maps known Odoo exception types to user-friendly messages.
    """

    _PATH_RE = re.compile(r"/[\w/.\-]+\.py(?::\d+)?")
    _SQL_RE = re.compile(
        r"\b(?:SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|"
        r"DELETE\s+FROM|CREATE\s+(?:TABLE|INDEX|VIEW)|"
        r"ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|VIEW))\b[^;]*",
        re.IGNORECASE,
    )
    _TRACEBACK_RE = re.compile(
        r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)",
        re.DOTALL,
    )
    _DB_RE = re.compile(
        r"(?:database|db)[\"'\s:=]+[\"']?[\w\-_.]+[\"']?",
        re.IGNORECASE,
    )
    _URL_RE = re.compile(r"https?://[^\s]+")

    _ERROR_MAP: dict[str, str] = {
        "odoo.exceptions.AccessError": ("Access denied: insufficient permissions"),
        "odoo.exceptions.AccessDenied": ("Authentication failed: invalid credentials"),
        "odoo.exceptions.ValidationError": (
            "Validation error: please check your input"
        ),
        "odoo.exceptions.UserError": "Operation failed",
        "odoo.exceptions.MissingError": "Record not found",
    }

    def sanitize(self, error_message: str) -> str:
        """Clean an error message for external consumption.

        Strips file paths, SQL, tracebacks, and database references.
        Maps known Odoo errors to user-friendly messages.
        """
        if not error_message:
            return "An unexpected error occurred"

        # Check for known Odoo exception patterns
        for exc_name, friendly in self._ERROR_MAP.items():
            if exc_name in error_message:
                # Try to extract the user-visible part after the exception name
                parts = error_message.split(exc_name, 1)
                if len(parts) > 1:
                    remainder = parts[1].strip().lstrip(":").strip()
                    # If there's a meaningful remainder, include it
                    if (
                        remainder
                        and len(remainder) < 200
                        and not self._contains_internals(remainder)
                    ):
                        return f"{friendly}: {remainder}"
                return friendly

        # Strip internals from the message
        cleaned = error_message

        # Remove tracebacks first (they contain paths and other internals)
        cleaned = self._TRACEBACK_RE.sub("[internal error details removed]", cleaned)

        # Remove file paths
        cleaned = self._PATH_RE.sub("[path removed]", cleaned)

        # Remove SQL
        cleaned = self._SQL_RE.sub("[query removed]", cleaned)

        # Remove database references
        cleaned = self._DB_RE.sub("[db reference removed]", cleaned)

        # Remove internal URLs
        cleaned = self._URL_RE.sub("[internal]", cleaned)

        # Clean up whitespace
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = cleaned.strip()

        if not cleaned:
            return "An unexpected error occurred"

        return cleaned

    def sanitize_exception(self, exc: Exception) -> str:
        """Convert an exception to a safe error message."""
        exc_type = type(exc).__qualname__
        module = type(exc).__module__ or ""
        full_name = f"{module}.{exc_type}" if module else exc_type

        # Check if this is a known Odoo exception
        if full_name in self._ERROR_MAP:
            msg = str(exc).strip()
            if msg and len(msg) < 200 and not self._contains_internals(msg):
                return f"{self._ERROR_MAP[full_name]}: {msg}"
            return self._ERROR_MAP[full_name]

        # Generic exception: sanitize the message
        return self.sanitize(str(exc))

    def _contains_internals(self, text: str) -> bool:
        """Check if text contains internal details that should be stripped."""
        return bool(
            self._PATH_RE.search(text)
            or self._SQL_RE.search(text)
            or self._TRACEBACK_RE.search(text)
            or self._DB_RE.search(text)
            or self._URL_RE.search(text)
        )
