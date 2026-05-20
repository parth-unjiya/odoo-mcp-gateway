# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-05-20

Major release — enterprise multi-user HTTP transport, OAuth 2.1 bearer auth, Plugin SDK 1.0, observability stack, and 8 new MCP-spec capabilities. **Strict backward-compat**: every v0.2.x stdio user upgrades with **zero config changes**.

### Pre-release audit fixes (this branch)

Four blockers caught by the pre-release audit and fixed inline before tagging:

1. **HTTP-mode login rate limiter now scoped per remote IP.** A new `_current_http_client` ContextVar (set by `SessionResolverMiddleware` from `scope["client"]`, or the first hop of `X-Forwarded-For` when `MCP_TRUST_PROXY=true`) is the source key for the `LoginIpRateLimiter`. Before, every failed login from every peer collided into a single per-process bucket, so 30 bad-password attempts from any attacker locked out every legitimate caller for 15 min.
2. **`/metrics` now requires bearer auth by default.** New env vars `MCP_METRICS_TOKEN` + `MCP_METRICS_REQUIRE_AUTH=true` (default ON). Secure-by-default with a loud 503 when auth is required but no token has been provisioned. Operators behind a network ACL can flip `MCP_METRICS_REQUIRE_AUTH=false`. `/health` and `/ready` stay anonymous (load-balancer probes).
3. **OAuth verifier now actually wired into `_build_fastmcp`.** New env vars `MCP_OAUTH_ENABLED`, `MCP_OAUTH_ISSUER`, `MCP_OAUTH_AUDIENCE`, `MCP_OAUTH_JWKS_URI`, `MCP_OAUTH_REQUIRED_SCOPES`. When enabled, the gateway accepts BOTH opaque login-issued tokens AND IdP-issued JWTs via `CompositeTokenVerifier`. Fail-fast `ConfigurationError` at startup when enabled without issuer/audience.
4. **Plugin lifecycle hooks now fire end-to-end.** Previously `register()` was the only hook called: `pre_register` / `post_register` / `pre_call` / `post_call` / `on_session_close` were all dead code despite the dispatcher methods existing. New `PluginLifecycleMiddleware` mounted on `FastMCP` brackets every tool call with `dispatch_pre_call` / `dispatch_post_call`; session eviction in `tools/auth.py` now calls `dispatch_on_session_close`; `PluginRegistry.activate()` brackets each plugin's `register()` with `pre_register` / `post_register`. `on_external_event` remains SDK contract only — it's operator-driven and the gateway calls no internal trigger; operators wanting webhook-style fan-out can call `dispatch_on_external_event` directly from plugin code (v0.4.0 wires the Odoo-bus listener).

### Highlights

- **Streamable HTTP transport** with bearer-token auth, per-request session middleware via ContextVar + ASGI, and `asyncio.Lock`-serialised session swaps.
- **OAuth 2.1 (additive)** — IdP-issued JWT validation via `authlib`, email-claim → `res.users.login` mapping, 5-scope intersection, JWKS TTL cache.
- **Plugin SDK 1.0** — typed `Protocol` + 7 lifecycle hooks + version compat range (`plugin_sdk_version`) with two-tier policy (same-major warn, different-major refuse).
- **Tool annotations** on every tool (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
- **Completions** for model / method / record_id arguments (Odoo discoverability "magic" — no competitor ships this).
- **Elicitation** on `create_record` when required fields are missing.
- **Bulk operations** (`bulk_create`, `bulk_update`) — single-transaction-atomic chunks with progress notifications.
- **Observability stack** — `/health`, `/ready`, `/metrics` (Prometheus), structured JSON logs (structlog), OpenTelemetry tracing with httpx auto-instrumentation.
- **`domain_builder.validate_domain` rewrite** — ports Odoo's own `normalize_domain` algorithm; rejects malformed inputs the old depth-counter accepted.

### New features (by ADR)

**ADR-001 — HTTP per-request session middleware**
- `core/auth/middleware.py::SessionResolverMiddleware` projects the MCP SDK's `auth_context_var` into our existing `_current_session_key` ContextVar.
- `core/auth/token_verifier.py::OdooTokenVerifier` implements the SDK's `TokenVerifier` protocol against `GatewayContext.token_index`.
- `__main__._run_streamable_http` drives uvicorn directly, mounting our middleware AFTER the SDK's `AuthContextMiddleware`.
- `_resolve_session_auth_manager` refuses to fall back to a residual session when the contextvar is stale (closes the v0.2.2 TOCTOU class).

**ADR-002 — Bearer token issuance**
- `GatewayContext.token_index` maps opaque tokens → session_keys. Rotation on re-login; revocation on session eviction or process cleanup.
- `login` tool response includes `bearer_token` + `token_type` fields. Stdio callers ignore them; HTTP callers send them as `Authorization: Bearer`.
- Eviction-revokes-tokens prevents dangling tokens from outliving their `AuthManager`.

**ADR-003 — Plugin SDK 1.0**
- `plugins/sdk.py` — `@runtime_checkable Protocol` (`OdooMcpPlugin`); `PluginContext` facade exposing rbac / restrictions / audit / field_inspector / model_registry / version_adapter; `check_plugin_sdk_compat()` with two-tier failure.
- `OdooPlugin` ABC upgraded with `plugin_sdk_version` default + 7 lifecycle hooks (no-op defaults): `pre_register`, `post_register`, `pre_call`, `post_call`, `on_session_close`, `on_external_event` (reserved for v0.4.0 webhooks).
- `PluginRegistry.dispatch_*` methods with per-plugin error isolation — one misbehaving plugin can't crash the dispatch path.
- 4 built-in plugins (HR / Sales / Project / Helpdesk) declare `plugin_sdk_version = ">=1.0,<2.0"` explicitly.
- New `docs/PLUGIN_AUTHORING.md` (200-line guide).

**ADR-004 — `domain_builder.validate_domain` rewrite**
- Ports Odoo's own `normalize_domain` algorithm (LGPL-3, attributed). Single-pass O(n), expected-counter + arity stack.
- Correctly handles binary `&` / `|` vs unary `!`. Computes true tree depth.
- **Strict mode**: rejects implicit `&` between unjoined subtrees (Odoo accepts; we don't).
- `MAX_DOMAIN_DEPTH` lowered from 10 to 8 (every observed legitimate domain is depth ≤ 5).
- +13 regression tests covering arity violations, operator-only domains, trailing garbage, mixed nesting, valid complex polish-form.

**ADR-005 — OAuth 2.1 additive auth (`[oauth]` extra)**
- `core/auth/oauth_verifier.py::OAuthJwtVerifier` validates IdP JWTs via `authlib.jose` (RS256 / ES256 only — HS256 prohibited by OAuth 2.1 for public clients).
- Validates `iss`, `aud == gateway_url`, `exp` with 30s leeway. JWKS cached 10 minutes.
- Email-claim → `res.users.login` mapping (zero-config for 90% of Odoo deployments).
- 5-scope hierarchy: `odoo.read / write / delete / workflow / admin`.
- `CompositeTokenVerifier` chains opaque (Sprint 1) + JWT verifiers for migration deployments.
- Stdio mode UNCHANGED — OAuth only activates on `streamable-http` with an IdP issuer wired.

**ADR-006 — Observability (`[observability]` extra)**
- `core/observability/health.py` — `/health` liveness (no external deps) + `/ready` readiness (Odoo-probe TTL-cached at 10s to avoid LB-poll self-DoS).
- `core/observability/metrics.py` — `MetricsRegistry` with 10 standard counters/histograms/gauges + `/metrics` Prometheus endpoint.
- `core/observability/tracing.py` — `configure_tracing()` + `tool_span()` with hashed `mcp.session.id` (no raw PII), `odoo.uid`, httpx auto-instrumentation when `opentelemetry-instrumentation-httpx` is installed.
- `core/observability/structured_logging.py` — `configure_structlog()` with ContextVar auto-injection.
- Soft-imports throughout — without the extra installed, every observability call is a no-op.

**ADR-007 — Tool annotations**
- `tools/annotations.py` central per-tool map (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
- `apply_pending_annotations(server)` walks the tool registry once after `create_server` finishes and attaches annotations.
- Conformance test ensures every registered tool has a map entry — new tools can't ship without annotations.

**ADR-008 — Elicitation on `create_record`**
- `tools/elicitation.py::detect_missing_required_fields` + `elicit_missing_fields`.
- `create_record` now accepts optional `ctx: Context`. When required fields are missing, the server elicits them via `elicitation/create`. Clients that don't support elicitation get the standard error.
- Field schema fetched ONCE per create and shared with `_validate_writable_fields` (no duplicate `fields_get` round-trip).

**ADR-009 — Completions**
- `core/discovery/completions.py` registers a `completion/complete` handler.
- `model` / `model_name` args → restriction-filtered list from `ModelRegistry`, prefix-rank above substring; YAML fallback when registry not yet populated.
- `method` / `action` args → `model_access.yaml::allowed_methods`, narrowed by model context.
- `record_id` args → syntax hint (no Odoo round-trip per keystroke).
- Capped at 50 values, exception-safe (typeahead can never crash).

**ADR-010 — Bulk operations**
- `tools/bulk.py::bulk_create` + `bulk_update` — single-transaction-atomic chunks via Odoo's native `execute_kw(model, 'create', [records])` / `execute_kw(model, 'write', [ids, values])`.
- Chunks NOT atomic across each other (documented). `chunk_size` default 200, hard cap `_MAX_TOTAL_RECORDS = 5_000`.
- `bulk_update` dedupes `record_ids` to prevent workload amplification.
- Partial-state diagnostic on chunk failure (`partial_ids` / `completed_chunks` / `total_chunks`).
- Progress notifications via `ctx.report_progress(completed, total, message)` per chunk (silent no-op for clients that don't support).
- Full security pipeline runs per-record — NO shortcuts for batch performance.
- **Rejected**: `transaction_begin` / `transaction_commit` (would require Odoo-side module).

### Backward compatibility

| Scenario | Migration |
|---|---|
| Stdio user on Claude Desktop / Code | **Zero changes.** `pip install --upgrade odoo-mcp-gateway` — bearer_token field appears in login response but is ignored by stdio callers. |
| HTTP-transport deployment | New: send `Authorization: Bearer <token>` on every request after login. Old session-cookie path no longer needed. |
| External plugin author | Add `plugin_sdk_version = ">=1.0,<2.0"` to plugin class. Existing `OdooPlugin(ABC)` base still works (default declares this). |
| YAML configs | All v0.2.x YAML configs work unchanged. OAuth + scope additions are opt-in. |

### New optional dependencies

```toml
[project.optional-dependencies]
observability = [
    "prometheus-client>=0.20",
    "structlog>=24.1",
    "opentelemetry-api>=1.25",
    "opentelemetry-sdk>=1.25",
    "opentelemetry-instrumentation-httpx>=0.46b0",
]
oauth = [
    "authlib>=1.6",
]
```

Install with `pip install odoo-mcp-gateway[observability,oauth]` for the full stack.

### Tests + quality

- **1,827 tests passing** (1,675 → 1,827, +152 across 6 sprints). Coverage 90.46% (≥90% gate).
- Ruff check + format + mypy strict all clean across 191 files / 77 source files.
- Python 3.10, 3.11, 3.12, 3.13 all green in CI matrix.

### Deferred to v0.4.0

- Webhooks (Odoo bus → `notifications/resources/updated` push channel). The `SubscriptionTracker` API surface ships in v0.3.0 so plugin authors can write code against it now.
- `mcp_field_cache_hits/misses` + `odoo_rpc_duration_seconds` metric wiring — needs gateway-ref plumbing into `FieldInspector` and the RPC clients.
- Dynamic Client Registration (RFC 7591).
- Fine-grained per-model OAuth scopes (`odoo.read.sale.order`).
- "Odoo as OAuth provider" mode.
- SaaS anonymous multi-tenant (separate product).
- DXT one-click Claude Desktop install bundle.
- Async tasks primitive (spec still experimental).

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
