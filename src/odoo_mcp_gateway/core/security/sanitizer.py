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
    # Broadened URL stripper: matches http(s), ftp(s), file, ldap(s),
    # gopher, data, and (v0.3.3 LOW-2) dict, tftp, jar, chrome URIs.
    # file:// was the original motivation for blocking ir.attachment
    # (SSRF / path-traversal); without matching it here, an Odoo error
    # referencing a file:// path slipped through unredacted.  The extra
    # schemes (dict/tftp/jar/chrome) are defence-in-depth — they are
    # classic SSRF-bypass vectors that an attacker might smuggle into
    # an error message expecting the sanitiser to miss them.
    #
    # Note on URL-encoded / Unicode tricks (e.g. ``http%3a%2f%2f``,
    # ``http：//``): we deliberately do NOT URL-decode or NFKC-normalise
    # the input before matching, because doing so could distort the
    # human-readable error.  These bypasses are tracked as a follow-up
    # if a real-world report surfaces them.
    _URL_RE = re.compile(
        r"(?:https?|ftps?|file|ldaps?|gopher|data|dict|tftp|jar|chrome)"
        r":[^\s'\"<>)]+",
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
    # UAT v0.3.3 LOW: Odoo appends an internal repr to MissingError /
    # AccessError messages of the form ``\n(Record: res.partner(1,), User: 6)``
    # — the ``User: <uid>`` portion is an EFFECTIVE-uid leak (it does not
    # necessarily match the caller's session uid, e.g. an internal sudo()
    # bumped it). Strip the ``, User: <uid>`` fragment while keeping the
    # model+ids context, which is genuinely useful debugging info for the
    # caller. The leading newline + opening parenthesis are matched so we
    # don't accidentally chew a legitimate trailing "(...)" annotation
    # that the API surface added intentionally.
    # Note: Odoo's record repr embeds its own parentheses for the id
    # tuple (e.g. ``res.partner(42,)``), so a naive ``[^)]+?`` capture
    # bails before the ``, User:`` marker. We instead match ``\(Record:``,
    # then ANY chars (non-greedy, no newline), then the literal
    # ``, User: <digits>)``. This collapses the entire repr block
    # including the inner parentheses cleanly.
    _ODOO_RECORD_USER_RE = re.compile(
        r"\n?\(Record:\s*(.+?),\s*User:\s*\d+\)",
    )

    # UAT v0.3.3 #5e (systemic): Odoo's stock ACL-denial message leaks
    # three categories of internal detail when it reaches an MCP client:
    #
    # 1. The model technical name in parentheses after the display name
    #    — e.g. ``'Public Employee' (hr.employee.public)``. Display name
    #    is fine for debuggability; the technical name is an internal
    #    routing detail that callers should not learn.
    # 2. The "This operation is allowed for the following groups:" block
    #    + the bullet list of group display names / XML IDs that follows
    #    — e.g. ``- Administration / Access Rights``. This is RBAC policy
    #    information; revealing which groups exist tells an attacker how
    #    the customer's deployment is provisioned.
    # 3. The closing "Contact your administrator to request access if
    #    necessary." line — purely operational guidance that's the
    #    integrator's choice to surface (or not), never Odoo's.
    #
    # Canonical Odoo source string (16 / 17 / 18 / 19 are all equivalent
    # modulo whitespace / translation):
    #
    #     You are not allowed to access '<Display Name>' (<model.tech>)
    #     records.
    #
    #     This operation is allowed for the following groups:
    #     \t- <Group A>
    #     \t- <Group B>
    #
    #     Contact your administrator to request access if necessary.
    #
    # **Option A** (chosen): strip the technical name, the group block,
    # and the administrator-contact tail. Keep ``You are not allowed to
    # access '<Display Name>' records.`` because the display name is
    # already part of Odoo's user-facing UX (it shows in the web client
    # too) and gives the caller enough context to file a sensible ticket
    # without revealing internals. Option B (fully generic message) was
    # considered but rejected — it hurts admin/dev debugging without
    # adding security value; the display name is not a secret.
    #
    # The three sub-patterns below run in order; each is independent so
    # an ACL message that's missing one section still gets the others
    # stripped. Patterns are anchored against the canonical wording but
    # tolerate whitespace / leading-blank-line variations.
    _ODOO_ACL_TECH_NAME_RE = re.compile(
        r"(You are not allowed to access\s+'[^']+')"
        r"\s*\([\w.]+\)\s*"
        r"(records?\.)",
        re.IGNORECASE,
    )
    _ODOO_ACL_GROUPS_BLOCK_RE = re.compile(
        r"\n+\s*This operation is allowed for the following groups:\s*"
        r"(?:\n[\t ]*[-*]\s*[^\n]+)+",
        re.IGNORECASE,
    )
    _ODOO_ACL_CONTACT_ADMIN_RE = re.compile(
        r"\n+\s*Contact your administrator[^\n]*\.?",
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

        # UAT v0.3.3 LOW: scrub Odoo's internal ``\n(Record: model(ids,),
        # User: <uid>)`` repr BEFORE the friendly-mapping branch — that
        # branch uses the message body verbatim as the "remainder" after
        # the exception name, so a User-uid leak would otherwise survive
        # the mapping. Keep the model+ids portion for debuggability, drop
        # the User-uid portion (it's an effective-uid leak that may not
        # even match the caller's session uid).
        error_message = self._ODOO_RECORD_USER_RE.sub(
            r" (Record: \1)",
            error_message,
        )

        # UAT v0.3.3 #5e (systemic): strip Odoo's stock ACL-denial
        # boilerplate (model technical name in parens, the "allowed
        # groups" bullet block, and the "Contact your administrator"
        # tail). This runs BEFORE the friendly-mapping branch so the
        # remainder passed to the error map is already clean — and
        # also BEFORE the generic cleanup so the group lines never
        # reach the path/SQL/URL strippers (some group names contain
        # ``/`` which the path regex would mis-treat).
        error_message = self._ODOO_ACL_TECH_NAME_RE.sub(r"\1 \2", error_message)
        error_message = self._ODOO_ACL_GROUPS_BLOCK_RE.sub("", error_message)
        error_message = self._ODOO_ACL_CONTACT_ADMIN_RE.sub("", error_message)

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
            # UAT v0.3.3 #5e (systemic): scrub Odoo ACL boilerplate +
            # the (Record:, User:) repr from the raw message BEFORE
            # checking length / internals. The raw ACL string is
            # ~250 chars and contains group-name "/" tokens that the
            # path-regex flags as "internal", forcing the bare friendly
            # fallback and losing the display-name context. Scrubbing
            # first means we can keep the useful display-name portion
            # while still passing the safety gate.
            scrubbed = str(exc)
            scrubbed = self._ODOO_RECORD_USER_RE.sub(r" (Record: \1)", scrubbed)
            scrubbed = self._ODOO_ACL_TECH_NAME_RE.sub(r"\1 \2", scrubbed)
            scrubbed = self._ODOO_ACL_GROUPS_BLOCK_RE.sub("", scrubbed)
            scrubbed = self._ODOO_ACL_CONTACT_ADMIN_RE.sub("", scrubbed)
            msg = scrubbed.strip()
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
