# Changelog

All notable changes to the Shadownet Python SDK (`shadownet` on PyPI) are
recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [PEP 440](https://peps.python.org/pep-0440/) versioning. Releases
track the protocol version they implement; while the spec is at `v0.1` the
SDK ships as `0.x.y`. In the monorepo, tags use the
`python-sdk/vX.Y.Z` prefix.

## [Unreleased]

## [0.3.1] — 2026-05-19

### Added

- New module `shadownet.connect.redeem` for client-side handoff
  redemption:
  - `redeem_handoff(http, *, base_url, code) -> str` — POSTs to
    `<base>/v1/account/connect/handoff/<code>` and returns the embedded
    bearer token. Single-use; the server returns 404 on the second call.
  - `redeem_connect_url(http, connect_url, *, store=None) -> (base_url, token)`
    — resolves any `shadownet://connect` URL (inline or handoff) to its
    `(base_url, token)` pair. When a `TokenStore` is supplied, handoff
    URLs are checked against the cache before contacting the server, so
    a host agent can call this on every start without burning the
    single-use code.
  - `HandoffRedemptionError` for transport / 404 / non-JSON failures.
- New module `shadownet.connect.tokens` for persisting redeemed tokens
  across host-agent restarts:
  - `TokenStore` Protocol (`load`, `save`).
  - `KeyringTokenStore` — recommended default, backed by the OS secret
    store (Login keychain on macOS, Secret Service on Linux, Credential
    Manager on Windows) via the optional `keyring` dependency.
  - `FileTokenStore` — fallback for environments without an OS secret
    store. Writes 0o600 JSON files keyed by `sha256(connect_url)` under
    the OS state directory
    (`~/Library/Application Support/shadownet/handoff-tokens/` on macOS,
    `$XDG_STATE_HOME/shadownet/handoff-tokens/` on Linux,
    `%LOCALAPPDATA%\shadownet\handoff-tokens\` on Windows).
  - `default_token_store()` returns `KeyringTokenStore` if available,
    else `FileTokenStore`.
  - `default_store_path()` for callers that need the file fallback's
    root.

### Notes

- All additions are additive. No breaking changes to the 0.3.0 public
  API.

[0.3.1]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.3.1

## [0.3.0] — 2026-05-11

### Added

- New module `shadownet.connect` for one-token install bootstrap
  (RFC-0007 amendments A–D, draft) — CLIENT side helpers:
  - `fetch_integration_bundle(http, base_url, token) -> IntegrationBundle`
    — fetches the per-tenant `/v1/account/me/integration-bundle` endpoint
    (RFC-0007 amendment A), returning the canonical bootstrap payload
    (DID, shadowname, MCP endpoint, supported features, tool/event
    names, version) plus the optional webhook secret.
  - `parse_connect_url(url) -> ConnectURL` and `format_connect_url(...)`
    — symmetric helpers for the standardized `shadownet://connect?…` URL
    scheme (RFC-0007 amendment B). Supports both inline (`?token=`) and
    handoff (`?handoff=`) forms.
  - `ShadownetMCPSession(base_url, shadowname, token)` — async-context
    wrapper around `mcp.ClientSession` for a Shadownet sidecar's MCP
    endpoint. Provides `call_tool(name, args)` proxying and
    `inbox_loop(handler, *, timeout_seconds, on_error)` — a long-poll
    loop over the new `social_inbox_wait` MCP tool (RFC-0007 amendment
    D). Handles `last_event_id` cursor advancement and exponential
    backoff on transient errors. Used by the Hermes plugin and the
    Claude Code background monitor for NAT-free inbound delivery on
    hosts whose MCP SDK can't dispatch custom notifications.
- `shadownet.connect.errors` adds `ConnectError`, `BundleFetchError`,
  `BundleSchemaError`, `ConnectURLInvalid`, `MCPSessionError`.
- New module `shadownet.connect.fastapi` (behind the `[fastapi]` extra)
  — SERVER side helpers for sidecar implementations (hermes-social,
  shadownet-cloud, …):
  - `build_connect_router(*, bundle_builder, host_templates, handoff_resolver)`
    returns a `fastapi.APIRouter` exposing the bundle endpoint
    (`GET /v1/account/me/integration-bundle`, with a legacy alias for
    the previous `/tenants/me/` path), `<base>/connect/<host>` content-
    negotiated install pages, `<base>/connect/raw` JSON, and an optional
    `POST /v1/account/connect/handoff/{code}` resolver.
  - `DEFAULT_HOST_TEMPLATES` ships with `hermes-agent` and `raw` out of
    the box; operators add `claude-code`, `openclaw`, `cursor`,
    `continue`, … by passing their own `HostTemplate` instances.
- `shadownet.mcp.tools` adds `InboxWaitInput`, `InboxWaitEvent`,
  `InboxWaitOutput` and the `INBOX_WAIT_MAX_TIMEOUT_SECONDS` (90) clamp
  constant (RFC-0007 amendment D).
- `shadownet.mcp.protocol.Sidecar` Protocol gains
  `social_inbox_wait(input) -> InboxWaitOutput` for sidecar implementors.
- `shadownet.mcp.register.register_shadownet_tools` now accepts
  `"inbox_wait"` in `include_optional`. When opted in, the tool is
  registered with server-side timeout clamping at 90 seconds, dispatching
  to `sidecar.social_inbox_wait`.

### Notes

- All additions are additive. No breaking changes to the 0.2.x public
  API. Existing webhook signing (`X-Shadownet-Sidecar-Sig` +
  `X-Webhook-Signature` compatibility header) and `verify_webhook` are
  unchanged — webhooks remain the canonical transport for
  sidecar-to-sidecar delivery, OpenClaw plugins, and any non-MCP
  integration.

[0.3.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.3.0

## [0.2.1] — 2026-05-11

### Added

- `webhook.build_webhook_headers` gains an `include_generic_hmac: bool = False`
  kwarg (RFC-0007 §Compatibility headers). When set, the builder also emits
  `X-Webhook-Signature: <raw hex HMAC-SHA256>` alongside the canonical
  `X-Shadownet-Sidecar-Sig`/`-Ts`/`-Id` headers. This matches the pattern used
  by Hermes Agent webhooks, OpenClaw plugins, and similar generic-HMAC
  adapters. Default is `False` — opting in is one kwarg, fully
  backwards-compatible. Receivers validating only the compatibility header
  lose the `Ts`-bound replay defense (RFC-0007 still requires they check
  `X-Shadownet-Sidecar-Ts` or document the loss); the explicit kwarg keeps
  that trade-off visible at the call site.

[0.2.1]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.2.1

## [0.2.0] — 2026-05-10

### Changed

- Project moved from
  [`shadownet-protocol/shadownet-py`](https://github.com/shadownet-protocol/shadownet-py)
  into the [`shadownet-protocol/shadownet`](https://github.com/shadownet-protocol/shadownet)
  monorepo at `python-sdk/`. The PyPI distribution name (`shadownet`), the
  importable module (`shadownet`), and the public API are unchanged —
  `pip install shadownet` and existing imports continue to work without
  modification. Repository URLs in package metadata now point at the new
  monorepo. See [`MIGRATION.md`](../../MIGRATION.md) for the full notes.

## [0.1.3] — 2026-05-03

A fourth interop bug surfaced by `shadownet-conformance` after v0.1.2 shipped:
the CSR JWT was still missing its `kid` header, parallel to the subject-auth
fix in v0.1.2.

### Fixed

- `sca.csr.build_csr` now sets `kid` in the JWT header (defaults to the bare
  holder DID; explicit override via the `kid=` keyword argument). Matches the
  pattern landed for `build_subject_auth` and `mint_session_token` in v0.1.2.
  `SCAClient.request_issuance` picks this up automatically through the new
  default — no callsite change needed downstream.

### Added

- `CHANGELOG.md` (this file). The README now links here for per-release
  detail.

### Tests

- 2 additional regression tests in `tests/unit/test_v0_1_2_regressions.py`
  cover the new CSR `kid` default and override.

## [0.1.2] — 2026-05-03

Three interop bugs caught by `shadownet-conformance` against v0.1.1.

### Fixed

- `sca.csr.build_subject_auth` now sets `kid` in the JWT header per
  RFC-0004 §Common: subject authentication. Defaults to the bare holder DID;
  override via the new `kid=` keyword argument when a `did:web` controller
  has multiple verification methods.
- `a2a.session.mint_session_token` mirrors the change for symmetry. RFC-0006
  doesn't strictly require `kid` on session tokens, but stricter peer SDKs
  may; this keeps holder-signed JWTs consistent across the surface.
- `sca.policy.LevelPolicy.method` and `sca.client.ProofSession.method`
  drop the over-strict `^urn:` regex per RFC-0004 §Policy document
  (`method` is an "operator-defined URI" — any URI scheme is valid).

### Tests

- 7 new regression tests in `tests/unit/test_v0_1_2_regressions.py`
  pin every fix and the explicit `kid=` override paths.

## [0.1.1] — 2026-05-03

### Changed

- Switched canonical Shadownet domain placeholder from `shadownet.example`
  to `sh4dow.org` (the protocol's first registered domain). Affects the
  `SHADOWNET_VC_CONTEXT` constant in `shadownet.vc.credential` and every
  test/fixture/conformance vector that anchored against the old placeholder.
  Wire-format change: any peer that hardcoded the old context URL will not
  string-match credentials issued with this release.

## [0.1.0rc2] — 2026-05-03

### Fixed

- `release.yml` tag-version check now normalizes both sides through
  `packaging.version.Version` so the git tag (`v0.1.0-rc.1`) and the
  PEP 440 wheel version (`0.1.0rc1`) compare equal.

### Added

- `mypy --strict` job in CI; PEP 561 `py.typed` marker verified to ship
  in the wheel; coverage report wired into pytest defaults.
- Multi-Python matrix (3.12 + 3.13) and the `actionlint` workflow.

## [0.1.0rc1] — 2026-05-03

Initial pre-release. Implements the v0.1 RFC set:

- **DID** — `did:key` (local) and `did:web` (async, `Cache-Control`-aware,
  16 KiB cap) per RFC-0002.
- **Verifiable Credentials** — VC-JWT issuance and verification, freshness
  proofs, BitstringStatusList revocation (fail-closed above L1) per RFC-0003.
- **SCA client** — proof session + issuance + freshness + callback HMAC
  per RFC-0004.
- **SNS client** — async resolver with TTL and negative cache, signed
  records per RFC-0005.
- **A2A profile** — session token + Verifiable Presentation handshake;
  framework-agnostic verifier; optional FastAPI dependency per RFC-0006.
- **Webhooks** — outbound dispatcher with the spec retry schedule and
  degraded-state tracking, plus a receiver-side verifier per RFC-0007.
- **MCP** — Pydantic input/output models for every RFC-0007 tool, a
  `Sidecar` Protocol, and `register_shadownet_tools(server, sidecar)` to
  wire them onto a `FastMCP` instance.
- Fully `mypy --strict`-clean; ships `py.typed`; ruff lint+format clean;
  176 tests at the cut.

[Unreleased]: https://github.com/shadownet-protocol/shadownet/compare/python-sdk/v0.2.0...HEAD
[0.2.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.2.0
[0.1.3]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.0-rc.2...v0.1.1
[0.1.0rc2]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.0-rc.1...v0.1.0-rc.2
[0.1.0rc1]: https://github.com/shadownet-protocol/shadownet-py/releases/tag/v0.1.0-rc.1
