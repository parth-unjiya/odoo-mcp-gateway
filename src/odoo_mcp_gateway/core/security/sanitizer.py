"""Error sanitizer: cleans internal details from error messages."""

from __future__ import annotations

import re


class ErrorSanitizer:
    """Strips internal details from error messages before exposing to clients.

    Removes file paths, SQL queries, tracebacks, and database names.
    Maps known Odoo exception types to user-friendly messages.
    """

    # Match Linux *.py paths AND Windows-style C:\... paths (Odoo also
    # runs on Windows servers; their traceback file references leak
    # absolute Windows paths). Also catches .pyc/.so/.yml/.json/.xml
    # paths referenced by Odoo addons-not-found errors.
    _PATH_RE = re.compile(
        r"(?:/[\w/.\-]+\.(?:py|pyc|so|yml|yaml|json|xml)(?::\d+)?|"
        r"[A-Za-z]:[\\/](?:[\w.\-]+[\\/])+[\w.\-]+(?::\d+)?)"
    )
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
    # Broadened URL stripper: matches http(s), ftp, file, ldap(s), gopher,
    # and data: URIs. file:// was the original motivation for blocking
    # ir.attachment (SSRF / path-traversal); without matching it here, an
    # Odoo error referencing a file:// path slipped through unredacted.
    _URL_RE = re.compile(
        r"(?:https?|ftps?|file|ldaps?|gopher|data):[^\s'\"<>)]+",
        re.IGNORECASE,
    )

    # PostgreSQL-specific leakage patterns. A bad-type domain (e.g. comparing
    # a many2one to "not_an_int") makes Odoo bubble psycopg2 text up through
    # the JSON-RPC error, exposing schema names and SQL line offsets.
    # Match psycopg2 AND psycopg (the v3 successor used in Odoo 18+).
    _PG_ERROR_CLASS_RE = re.compile(
        r"psycopg2?(?:\.\w+)*\.\w+",
    )
    _PG_LINE_RE = re.compile(r"LINE \d+:[^\n]*")
    _PG_RELATION_RE = re.compile(
        r'relation "[^"]+" does not exist',
        re.IGNORECASE,
    )
    # Column / detail / context markers that psycopg2 prepends to multi-line
    # error reports. Strip the whole line, not just the keyword.
    _PG_DETAIL_RE = re.compile(
        r"(?m)^(?:DETAIL|HINT|CONTEXT|QUERY|STATEMENT):.*$",
    )
    # "invalid input syntax for type integer: \"foo\"" and friends —
    # leaks expected schema type for a column. Strip the whole clause.
    _PG_INVALID_INPUT_RE = re.compile(
        r"invalid input syntax for type \w+:?\s*\"[^\"]*\"",
        re.IGNORECASE,
    )
    # Column-name references in psycopg2 errors (column "xyz" of
    # relation "..." violates not-null constraint, etc.).
    _PG_COLUMN_RE = re.compile(
        r'column "[^"]+" of relation "[^"]+"',
        re.IGNORECASE,
    )

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

        # Werkzeug 404 boilerplate — replace with a friendlier message
        # that doesn't expose the transport-layer detail. This fires when
        # the gateway asks for a nonexistent Odoo endpoint / model.
        if (
            "404 Not Found" in error_message
            and "The requested URL was not found" in error_message
        ):
            return (
                "Model or endpoint not found. Verify the model name is "
                "correct and the corresponding Odoo module is installed."
            )

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

        # Remove PostgreSQL-specific noise (relation, line indicators,
        # DETAIL/HINT/CONTEXT/QUERY blocks, psycopg2 exception class names,
        # invalid-input-type leaks, column-of-relation references).
        # Order matters: strip relation/LINE/invalid-input/column before
        # generic patterns so the replacement text isn't itself re-matched
        # by _DB_RE.
        cleaned = self._PG_RELATION_RE.sub("[schema reference removed]", cleaned)
        cleaned = self._PG_COLUMN_RE.sub("[column reference removed]", cleaned)
        cleaned = self._PG_LINE_RE.sub("[sql excerpt removed]", cleaned)
        cleaned = self._PG_INVALID_INPUT_RE.sub("[invalid input]", cleaned)
        cleaned = self._PG_DETAIL_RE.sub("[pg detail removed]", cleaned)
        cleaned = self._PG_ERROR_CLASS_RE.sub("[db error]", cleaned)

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
            or self._PG_ERROR_CLASS_RE.search(text)
            or self._PG_LINE_RE.search(text)
            or self._PG_RELATION_RE.search(text)
            or self._PG_COLUMN_RE.search(text)
            or self._PG_INVALID_INPUT_RE.search(text)
            or self._PG_DETAIL_RE.search(text)
        )
