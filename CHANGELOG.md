# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-04-29

### Security

Comprehensive security hardening based on multi-pass swarm audit. 21 fixes spanning critical, major, and quality categories.

**Brute-force protection**
- New `LoginRateLimiter` — 5 failed logins per username → 5 minute lockout. Fixed-duration lockout cannot be extended by additional failures (closes DoS primitive where attacker could lock out a victim indefinitely with one attempt every <5 min).
- New `LoginIpRateLimiter` — 30 failures per source/IP → 15 minute lockout. Blocks username-rotation attacks where attacker cycles usernames to stay under per-username threshold.
- 10,000-entry cap with LRU eviction prevents memory exhaustion under sustained attack.

**Credential hardening**
- New `Credential` wrapper class with `__slots__`, leak-safe `__repr__`/`__str__`, explicit `.reveal()` accessor, `.clear()` on close. Passwords, session IDs, and API keys all wrapped — prevents leakage via `repr()`/traceback/logs.
- `JsonRpcClient.close()` and `XmlRpcClient.close()` now clear all stored credentials including `_uid`.

**Authorization hardening**
- `is_admin` now verified server-side via `res.users.has_group("base.group_system")` after authentication. Defends against tampered auth responses (proxy/MITM flipping the admin bit).
- Private methods (`_method`) blocked for everyone including admin unless explicitly whitelisted in `model_access.yaml`. Previously admin was a wildcard for underscore-prefixed Odoo internals — privilege escalation vector.
- `Settings.session_timeout_seconds` and `Settings.max_concurrent_sessions` now wired into `AuthManager` (were dead config — defaults of 1800/100 always applied).

**Hardcoded blocklist expansion**
- `_ALWAYS_BLOCKED_MODELS`: 17 → 32 entries. New: `res.users`, `ir.attachment`, `payment.token`, `payment.provider`, `base.automation`, `mail.template`, `mail.mail`, `auth.totp.wizard`, `auth.totp.device`, `res.users.log`, `ir.logging`, `iap.account`, `ir.exports`, `ir.exports.line`, `digest.digest`. YAML configs cannot weaken hardcoded blocks.
- `_ALWAYS_READ_ONLY_MODELS` (new): 8 entries — `mail.message`, `mail.followers`, `mail.activity`, `discuss.channel`, `mail.notification`, `mail.compose.message`, `mail.alias`, `discuss.channel.member`. Reads allowed (Odoo's `ir.rule` filters per-user), writes blocked for everyone to prevent message injection / mail spoofing / channel manipulation.
- `_ALWAYS_BLOCKED_WRITE_FIELDS` (new): 10 entries — `password`, `password_crypt`, `groups_id`, `totp_secret`, `signup_token`, `signup_type`, `signup_expiration`, `api_key`, `share`, `active`. Defense-in-depth even when no YAML config deployed.
- `_ALWAYS_BLOCKED_METHODS`: 18 → 29 entries. New: `name_create`, `load`, `import_data`, `export_data`, `flush_recordset`, `invalidate_recordset`, `_search_panel_select_range`, `_search_panel_select_multi_range`, `_search_panel_domain_image`, `_search`, `_read_progress_bar`.

**Tool-layer hardening**
- `get_onchange`: validates `fields` parameter via `_WRITE_FIELD_RE` (prevents path-traversal-like injection into RPC payload). Applies RBAC filtering on the changes dict before returning (sensitive fields from onchange results are now redacted).
- `read_group`: `limit` now defaults to 500 when `None` (was unlimited → memory exhaustion risk on large grouped queries).
- `search_read`: field-inspection failure falls back to `["id", "display_name"]` instead of `fields=None` (which let Odoo return all fields, leaking sensitive data).
- `JsonRpcClient.execute_kw`: retry now fires only on new `OdooSessionExpiredError` (mapped from `odoo.http.SessionExpiredException`), not generic `OdooAuthError`. Prevents double Odoo round-trips on legitimate access denials.

**Plugin hardening**
- All 16 plugin tools now call `check_plugin_modules()` for graceful degradation when required Odoo modules are missing.
- Helpdesk `get_my_tickets`: `state` parameter validated via allowlist regex (was passing user input into dotted-path domain).
- Helpdesk `create_ticket`: now verifies `user_id` survived RBAC sanitization. Returns an error if dropped (was silently creating unassigned tickets).
- `RBACManager.sanitize_write_values()`: new `return_dropped=True` overload returns `(sanitized_dict, dropped_fields)` for caller transparency. Backward-compatible.

### Added

- `dry_run` parameter on `create_record`, `update_record`, `delete_record`, `execute_method` — runs full validation/security/RBAC pipeline without executing the Odoo RPC call. Returns what would happen.
- `get_defaults` tool — preview Odoo default values for a model before calling `create_record`. RBAC-filtered.
- `get_onchange` tool — preview field side effects when changing a value. RBAC-filtered.
- Temporal grouping in `read_group`: `groupby=["create_date:month"]`, `date:quarter`, `date:week`, `date:day`, `date:year`. Whitelisted operators only — no SQL injection vectors.
- `OdooSessionExpiredError` exception class (subclass of `OdooAuthError`).
- `format_model_error()` extended with optional `alternate_models` parameter.

### Fixed

- 1 mypy error in `core/auth/manager.py` (session_id Credential type)
- Removed misleading `res.users` from `model_access.yaml.example` `admin_only` list (it's hardcoded blocked, listing it had no effect).
- Added hardcoded blocklist comment block to `restrictions.yaml.example` for operator clarity.
- Deduplicated three near-identical error-translation blocks in helpdesk plugin (now uses `format_model_error` with `alternate_models`).

### Documentation

- `_current_session_key` now has WARNING block documenting HTTP mode as single-tenant only until per-request middleware lands.
- README quickstart, security guardrail counts, tool counts all refreshed for v0.2.1.

### Tests

1,312 → 1,476 (+164). Lint clean (ruff), mypy strict clean. Coverage 93%.

5 new test files: `test_dry_run.py`, `test_get_defaults.py`, `test_get_onchange.py`, `test_login_rate_limit.py`, `test_plugin_degradation.py`.

## [0.2.0] - 2026-03-17

### Added
- Intelligent Workflow Engine (v2 core feature)
  - Workflow definitions with state machines for common Odoo models
  - `odoo://workflow/{model}` resource for workflow discovery
  - `get_create_requirements` tool — guides AI through record creation
  - `get_record_actions` tool — shows valid next actions for a record
  - 5 new workflow prompts: quote_to_invoice, employee_onboarding, ticket_lifecycle, purchase_to_receipt, lead_to_opportunity
  - Stock workflows for sale.order, purchase.order, hr.leave, helpdesk.ticket, crm.lead
- Session isolation for HTTP/streamable-http mode (per-MCP-session state)
- Session timeout enforcement with lazy eviction
- Admin detection via `has_group()` XML-RPC call (works in non-English Odoo)
- ConnectionManager integration with circuit breaker and retry for all RPC clients
- Plugin operation type declaration (plugins can register tool operation types for correct rate limiting)
- Built-in plugin entry points registered in pyproject.toml
- Version adapter extensions: `get_removed_fields()`, `get_renamed_fields()`, `get_state_field_overrides()`
- CHANGELOG.md
- SECURITY.md with vulnerability disclosure policy
- .env.example for Docker and native setup

### Fixed
- Version mismatch between `__init__.py` (0.1.0) and pyproject.toml (0.1.1)
- MCP_PORT default now 8080 (was 8000), matching README and Docker documentation
- Credentials (password, login, db) scrubbed from memory on client close
- Duplicate transport resolution in `__main__.py` removed (was bypassing Pydantic validation)
- Missing `hr.attendance`, `helpdesk.ticket`, `helpdesk.team` added to model_access.yaml.example
- gateway.yaml.example now clearly marked as reference-only (not loaded by application)
- restrictions.yaml.example notes hardcoded guardrail overlap

### Improved
- Test coverage for `__main__.py`, ErrorSanitizer, HR plugin, and resource handlers
- Plugin entry points registered for built-in plugins (HR, Sales, Project, Helpdesk)

## [0.1.1] - 2026-03-16

### Added
- Initial public release
- 27 MCP tools (11 core + 16 plugin)
- 5 MCP resources
- 7 MCP prompts
- Two-layer security: MCP restrictions (YAML) + Odoo ACLs
- 4 built-in domain plugins: HR, Sales, Project, Helpdesk
- Version-agnostic: Odoo 17, 18, 19
- 1,043 tests, 93% coverage
- CI/CD with GitHub Actions
- Docker support
- PyPI publishing
