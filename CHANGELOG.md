# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-05-19

### Security

Second hardening pass after multi-agent audit (security architecture, tools layer, plugins/workflow, test coverage). All 1,675 tests passing, ruff/mypy strict clean, 93% coverage. Live-verified end-to-end against real Odoo 17, 18, and 19 servers.

**Fix — non-admin users no longer over-blocked by RBAC (headline)**
- `_fetch_groups` now resolves group membership via `res.users.read([uid], ['groups_id'/'group_ids'/'all_group_ids'])` instead of searching `res.groups` (which broke on Odoo 19 — the `res.groups.users` field was renamed to `user_ids` there).
- XML IDs fetched via `res.groups.get_external_id()` (sudo'd in Odoo, callable by any authenticated user). Previously used `ir.model.data.search_read` which silently failed for non-admin users, leaving `group_xml_ids = []` and over-blocking every RBAC config keyed on technical IDs.
- Odoo 19 candidate `all_group_ids` is preferred so implied group memberships (e.g. `sales_team.group_sale_manager` implies `base.group_user`) reach the RBAC matcher.
- Legacy `ir.model.data` lookup kept as a defensive fallback for non-standard Odoo forks.

**Fix — failed logins no longer wipe the active session**
- Single-user-per-process eviction now runs ONLY after a successful login. A typo'd password or hostile wrong-credentials probe is a no-op on existing sessions; previously it kicked out the legitimate user (self/hostile DoS primitive).

**Fix — TOCTOU race on `auth_managers`**
- Eviction + registration + contextvar set are now wrapped in an `asyncio.Lock` so concurrent login() calls and in-flight tool calls cannot observe the dict mid-swap.
- `_resolve_auth_manager` / `_resolve_session_auth_manager` / `check_security_gate` now refuse to fall through to a residual session when the contextvar is set-but-stale (previously they silently rebound the request to whoever was the only remaining session — typically a different user).
- New `get_auth_context()` helper for atomic `(client, uid, is_admin, groups)` capture.

**Fix — workflow advertises only the version-correct method**
- `TransitionDef.min_version` / `max_version` filter transitions against the detected Odoo major version. `sale.order` advertises `action_done` only on v17 and `action_lock` only on v18+/v19. `purchase.order` advertises `button_done` on v17/v18 and `write:locked` on v19+ (`button_done` was removed there).
- `hr.leave` `action_validate` from the `confirm` state is now v17-only (deprecated on v18+).
- `_filter_transitions` consults `gateway.version_adapter.major_version`.

**Fix — helpdesk stage transitions no longer silently dropped**
- `_build_stage_based_response` dedupe key changed from `t.action` to `(t.action, t.target_state, t.label)`. Previously every helpdesk stage transition shared `write:stage_id` and only one survived; now all 4 (`new → in_progress`, `in_progress → solved`, `solved → closed`, `solved → in_progress`/reopen) reach the AI caller.

**Fix — execute_method dry_run now echoes sanitised payload**
- Response includes `sanitized_kwargs` and `stripped_context_keys` so callers see exactly which `_DANGEROUS_CONTEXT_KEYS` were filtered. Previously the dry_run preview hid the silent context stripping.
- `record_ids` deduped (caller submitting `[1]*500` no longer amplifies workload).

**Hardening — `DANGEROUS_CONTEXT_KEYS` promoted to shared utility**
- Lifted from `tools/crud.py` to `core/security/middleware.py` as public `DANGEROUS_CONTEXT_KEYS` set + `filter_dangerous_context_keys()` helper. Future plugin tools accepting `context` kwarg can apply the canonical filter rather than re-declaring their own.
- Re-exported from `core.security` for direct import.

**Hardening — sanitizer broadening**
- `_URL_RE` now matches `ftp://`, `file://`, `ldap://`, `gopher://`, `data:` URIs (closes the SSRF-leak gap that motivated the `ir.attachment` block originally).
- `_PATH_RE` matches Windows-style absolute paths (`C:\odoo\addons\…`) and non-`.py` references (`.xml`, `.yml`, `.so`, `.pyc`).
- `_PG_ERROR_CLASS_RE` matches both `psycopg2` and `psycopg` (v3, default on Odoo 18+).
- New `_PG_INVALID_INPUT_RE` strips `invalid input syntax for type integer: "foo"` leaks.
- New `_PG_COLUMN_RE` strips `column "x" of relation "y"` leaks.

**Hardening — stdio source-id is per-process**
- Login rate limit source bucket switched from literal `"stdio"` to `f"stdio:{os.getpid()}"`. Closes the self-DoS where 30 failed logins from any process locked out every process on the host for 15 minutes.

**Hardening — validator tightening**
- `_AGG_FIELD_RE` aggregate suffix is now a closed allowlist (`sum|avg|min|max|count|count_distinct|array_agg|bool_and|bool_or`). Previously accepted any `[a-z]+` suffix.
- `_validate_order` rejects the `field,direction` comma-typo (`"create_date,desc"` was silently accepted as two ascending fields).
- `_MODEL_PATTERN` rejects trailing dots and empty segments (`res.partner.`, `res..partner` no longer slip through YAML).
- `MAX_FIELD_TRAVERSAL` centralised in `utils/domain_builder` (previously duplicated as `field.count(".") > 3` in `_validate_order`).

**Hardening — get_onchange parity**
- Applies version-aware field rename (`_apply_value_renames`) to `values` and `changed_field` — previously a v18 caller hitting v19 silently forwarded the deprecated field name.
- Runs `restrictions.check_field_write` on `values` keys — previously a recon vector (probe whether a sensitive field triggers an onchange to infer existence).

**Hardening — update_record empty-required guard**
- Writing `""` to a required field is now rejected on BOTH create AND update (previously update silently no-op'd, surfacing as false-success — the canonical silent-success failure mode).

**Hardening — Credential constant-time equality**
- `Credential.__eq__` now uses `hmac.compare_digest`. Defence-in-depth even though no live call site uses `==` to gate access today.
- `AuthResult.group_xml_ids` uses `field(default_factory=list)` (was a `None` sentinel + `__post_init__` workaround).

**Hardening — AuditLogger default backend**
- Default changed from `"file"` (which wrote to `./audit.log` on the cwd) to `"logger"` (which routes through Python's logging framework — matches how `server.py` instantiates the logger in production).

**Hardening — XSS output sanitization**
- `format_records` markdown table now escapes angle brackets ONLY when dangerous patterns (script/iframe/onhandler/javascript:/data:) are detected. Benign content (`Smith & Sons`, math comparisons, URLs with `&`) passes through unchanged. Defense-in-depth for downstream chat clients that render Markdown as HTML.

### Configuration

- `rbac.yaml.example`: expanded with per-tool group requirements for sales/project/helpdesk/hr roles (both base-user and manager variants), so admins copying the example get sensible defaults without trial-and-error.
- `model_access.yaml.example`: added `account.journal`, `account.account`, `account.payment.method`, `account.analytic.account`, `account.analytic.line`, `hr.leave.allocation`, `helpdesk.stage`. Documented v17 vs v18+ allowed-method variants (`action_done` vs `action_lock`, `button_done` for purchase).

### Dependencies

- Pinned versions in dev environment to clear pip-audit alerts on transitive deps: `cryptography>=48.0.0`, `pyjwt>=2.12.1`, `python-multipart>=0.0.29`, `pygments>=2.20.0`, `pytest>=9.0.3`.

### Tests

- +82 new regression tests (1,593 → 1,675). Coverage 93%.
- New: `test_fetch_groups_non_admin`, `test_helpdesk_stage_dedupe`, `test_version_aware_transitions`, `test_execute_method_dry_run_echo`, `test_dangerous_context_helper`, `test_failed_login_preserves_session`, `test_helpers_stale_key_refuses`, `test_sanitizer_url_broadening`, `test_formatting_xss_escape`, `test_validator_tightening`, `test_onchange_parity`, `test_update_required_empty_string`, `test_credential_constant_time_eq`.
- Existing tests refactored where `_fetch_groups` RPC sequence changed.
- Live-verified end-to-end against Odoo 17 (community), 18 (community), 19 (enterprise): non-admin XML IDs visible, version-aware workflow filtering correct on each version, failed-login session preservation, RBAC matching for manager groups (`sales_team.group_sale_manager`).

### Architecture (not changed — deferred to v0.3.0)

- HTTP per-request session middleware (true multi-tenant) — single-user-per-process remains the supported stdio model.
- `domain_builder.validate_domain` polish-notation depth bug — contained by 50-leaf cap today.

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
