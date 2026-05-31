# Changelog

All notable changes to the Shadownet Python SDK (`shadownet` on PyPI) are
recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [PEP 440](https://peps.python.org/pep-0440/) versioning. Releases
track the protocol version they implement; while the spec is at `v0.1` the
SDK ships as `0.x.y`. In the monorepo, tags use the
`python-sdk/vX.Y.Z` prefix.

## [Unreleased]

## [0.5.0] — 2026-05-31

This is the **Shadownet v0.2 release**. Tracks the consolidated wire spec
(`shadownet-specs/feat/shadow1`) replacing v0.1's nine RFCs with three:
`rfcs/0001-shadownet.md` (wire), `rfcs/0002-shadownet-mcp.md` (MCP control
surface), `rfcs/0003-shadownet-onboarding.md` (onboarding URI). Direct
addressing (`shadow://key:z6Mk...@host:port`) is first-class peer to
Shadownames. The protocol extension URN moves to `urn:shadownet:0.2`.

**This is a breaking change. There are no v0.1 shims.** v0.1 users
should pin `shadownet<0.5`; downstream consumers (sidecars, plugins,
conformance) migrate at their own cadence. The v0.4.x series remains
on PyPI as the v0.1 release line.

### Fixed

- **RFC 0001 §11 agent-opacity leak in ``a2a.problem_response``**. The
  default RFC 7807 body now ships only ``type`` + ``title`` + ``status``;
  the exception's ``str()`` is no longer leaked as ``detail``. Receivers
  routinely raise with messages embedding the sender's Shadowname,
  ``messageId``, or stranger-vs-contact state — §11 forbids any of that
  reaching the wire. Pass ``include_detail=True`` (or an explicit curated
  ``detail=...``) to opt back in for trusted dev/CI use. Public receivers
  should keep both off.
- **Keyed-issuer credentials in ``ReceiverPipeline`` / ``AsyncReceiverPipeline``**.
  ``_resolve_issuer_key`` and ``_check_issuer_authorized_for_org`` now
  short-circuit when the issuer is a multibase public key (§3.3 / §6.6
  rule 1), matching ``default_issuer_key_resolver``. Previously the
  pipeline always called ``provider_lookup`` and a keyed-Hub credential
  failed because there is no DNS record for ``z6Mk...``.

### Added

- **§8.10 retry-with-remint helpers** (``send_with_retries`` /
  ``asend_with_retries``). Implements the canonical exponential-backoff +
  jitter loop with per-attempt envelope re-mint (fresh ``iat`` / ``exp``
  / ``messageId``). Accepts a ``builder`` closure so the caller controls
  what stays stable (body, ``to``, ``contextId``). Protocol-level
  rejections (``ShadownetWireError``) propagate immediately; only
  ``TransportError`` triggers a retry.
- **``a2a.TransportError``** — distinct from ``ParseError``. Raised by
  ``send_envelope`` / ``asend_envelope`` when the HTTP request never
  reached a peer (connect / read / DNS / TLS failure). Previously
  conflated under ``ParseError``, which made it impossible for retry
  callers to tell "the peer rejected me" from "we never got there".
- Async surface (dual sync/async). Every network-touching helper and the
  receiver pipeline now ship in two flavors so async consumers
  (FastAPI + asyncpg sidecars, anyio agents) can run the §8.6/§9 flow
  end-to-end without thread-pool detours. Additive only — sync surface
  unchanged.
  - `shadownet.provider`: `alookup_provider_record` (driven by
    `dns.asyncresolver`).
  - `shadownet.agentcard`: `afetch_agent_card_json`,
    `afetch_and_verify_agent_card`, `afetch_direct_agent_card_json`,
    `afetch_and_verify_direct_agent_card`.
  - `shadownet.status`: `afetch_status_list`, `acheck_revocation`.
  - `shadownet.csr`: `asubmit_csr`.
  - `shadownet.a2a`: `asend_envelope`.
  - `shadownet.onboarding`: `aredeem_handoff`, `arefresh_access_token`.
  - `shadownet.tls`: `make_pinned_httpx_async_client` and
    `_PinnedAsyncTransport` (httpx.AsyncHTTPTransport variant honoring
    RFC 0001 §5.3 pin policy).
  - `shadownet.credential`: `averify_credential` with awaitable
    `resolve_issuer_key` / `check_issuer_authorized_for_org` callbacks,
    plus `default_async_issuer_key_resolver` and
    `default_async_issuer_authorization_check`.
  - `shadownet.receiver`: `AsyncReceiverPipeline` driving the §8.6/§9
    flow over async plug-points `AsyncReplayCache`, `AsyncContactGraph`,
    `AsyncCredentialCache` (RAM impls supplied:
    `AsyncInMemoryReplayCache`, `AsyncInMemoryContactGraph`,
    `AsyncInMemoryCredentialCache`).
  All `a*` siblings accept an injected `httpx.AsyncClient` (own one
  when `None`) and mirror the sync error mapping verbatim.
- New module `shadownet.identifiers` exposing `Identifier`,
  `IssuerIdentifier`, `Shadowname`, `Domain`, `MultibasePublicKey`
  pydantic types plus discriminators (`is_shadowname`,
  `is_public_key_identifier`) and canonicalizers.
- New module `shadownet.addressing` parses RFC 0001 §3.2
  `shadow://` Shadow-addressing URIs (Shadowname or direct mode) plus
  optional `#sha256:` TLS pin fragments.
- New module `shadownet.jcs` implementing RFC 8785 JSON Canonicalization.
  Floats unsupported by design; this is what backs `msgHash`.
- New module `shadownet.provider` resolves `_shadownet.<domain>` TXT
  per RFC 0001 §4.2.
- New module `shadownet.agentcard` fetches and verifies A2A AgentCards
  per RFC 0001 §5 and A2A §8.4. Supports both Shadowname-mode
  (provider-signed at `<ep>/identity/<local>`) and direct-mode
  (self-signed at `<endpoint>/.well-known/agent-card.json`). Includes
  `build_signed_agent_card` and `build_direct_signed_agent_card` for
  provider / Sidecar implementations.
- New module `shadownet.credential` for `org_affiliation` JWT mint and
  verify per RFC 0001 §6. Keyed issuers and keyed orgs supported (§6.6
  rule 1 only path for keyed issuers).
- New module `shadownet.csr` for CSR mint, verify, and issuer client
  per RFC 0001 §6.5. Maps the §6.5 response statuses (200 / 409 / 403
  / 429) to typed exceptions.
- New module `shadownet.status` fetches the per-epoch revocation
  bitstring at `/.well-known/shadownet/status/<epoch>` and runs the
  `is_revoked` check per RFC 0001 §6.4. Big-endian within byte;
  fail-closed on any error.
- New module `shadownet.envelope` mints and verifies envelope JWS
  (`shadownet-env+jwt`) and computes `msgHash` per RFC 0001 §8.3 / §8.4.
- New module `shadownet.a2a` wraps A2A `message:send` around the
  envelope, maps RFC 7807 problem+json responses to typed
  `ShadownetWireError` subclasses per RFC 0001 §8.8, and exposes
  receiver-side helpers (`extract_envelope_jws`,
  `build_acceptance_response`).
- New module `shadownet.receiver` runs the full RFC 0001 §8.6
  validation pipeline + §9 classification (`inbox` /
  `stranger_review` / `rejected` + auto-add-on-outbound-initiated +
  same-provider-domain shortcut). Pluggable replay cache, contact
  graph, credential cache, AgentCard fetcher.
- New module `shadownet.tls` implements TLS pin verification for
  direct-mode connections per RFC 0001 §4.1 / §5.3.
  `make_pinned_httpx_client(direct_address)` returns an httpx.Client
  configured with verify_mode=CERT_NONE + TLSv1.3 + post-handshake
  fingerprint check (URI pin → TOFU recorded → first-use).
- New `shadownet.mcp` subpackage with typed pydantic models for every
  RFC 0002 §4 tool's input/output, the three v0.2 intent payload models
  (`CoordinateV1Data`, `ConfirmPlanV1Data`, `AcceptPlanV1Data`), the
  Path 1 notification event models, and a `ShadownetMCPClient` async
  wrapper around the upstream MCP streamable-HTTP client.
- New module `shadownet.onboarding` parses `shadow://connect?...` URIs
  per RFC 0003 §3, redeems handoff codes (§4), and refreshes access
  tokens (§7). Typed exception per HTTP status family.

### Changed

- **Hard cut.** The v0.1 modules `shadownet.{did, vc, sca, sns, a2a,
  mcp, connect, trust, webhook}` are removed entirely. There are no
  backwards-compatibility shims (project policy while protocol is at
  v0.1/v0.2). v0.1 users pin `shadownet<0.5`.
- `shadownet.trust` rewritten for the v0.2 flat trust store. The
  predicate language is gone. `AcceptancePolicy` is a `{fromContact,
  fromStranger}` pair of kind lists. Default trust store ships
  **empty** (RFC 0001 §7.1).
- `shadownet.errors` exposes only the root `ShadownetError`; the eight
  RFC 0001 §8.8 wire error codes live in `shadownet.a2a` as
  `ShadownetWireError` subclasses (`ParseError`, `SignatureError`,
  `CredsRequiredError`, `CredsRejectedError`, `PolicyError`,
  `ReplayError`, `UnknownRecipientError`, `RateLimitedError`).
- `shadownet.crypto` retained as-is (Ed25519 + JWS-EdDSA + multibase
  z-base58); the rest of the SDK builds on top.

### Removed

- DID method machinery (`did:key`, `did:web`, DID documents,
  `shadownet:delegatedIssuers`).
- W3C VC wrapping, Verifiable Presentations, freshness proofs,
  the L1/L2/L3/O1 personhood ladder. Credentials are plain JWTs
  with one kind: `org_affiliation`.
- SCA proof-session state machine and HMAC callback. CSR endpoint
  at `/.well-known/shadownet/issue` is idempotent within a ceremony.
- SNS (per-Shadowname signed JWT HTTP records). DNS TXT replaces it.
- Webhook delivery path. Inbound is MCP notifications (Path 1) or
  `inbox_wait` long-poll (Path 2, RECOMMENDED).
- `social_*` MCP tool name prefix. v0.2 tools are `identity`,
  `resolve`, `contacts`, `contact_detail`, `add_contact`, `grant`,
  `set_contact_profile`, `send`, `respond`, `coordinate`,
  `confirm_plan`, `accept_plan`, `inbox`, `inbox_wait`.
- v0.1 dropped tools: `social_set_webhook`, `social_present`,
  `social_audit`. Server-side MCP tool registration helpers are not
  in the SDK; live in shadownet-local.
- OAuth 2.1 profile (v0.1 RFC-0009). Opaque bearer tokens sufficient
  for the local-only control plane.

### Downstream impact

- **shadownet-conformance**: pinned at v0.1 SDK; needs migration
  before its next release. Tracking issue / branch TBD.
- **shadownet-hermes-plugin**: pinned at `shadownet>=0.4.1,<0.5`;
  needs adapter rewrite (RFC 0003 onboarding, RFC 0002 tool names,
  intent-URI dispatch replacing v0.1 `data_type` strings) before
  its next release.
- **integrations/plugins/{claude-code,openclaw}**: same.
- **integrations/skills/\***: already updated in this release cycle
  to the v0.2 tool surface.
- **shadownet-local** (reference Sidecar): pinned at v0.1 SDK; needs
  migration to the new receiver pipeline / MCP server-side tool set.
- **`core/`** (Go SDK + reference servers): tracks separately; the
  `feat/shadownet-0.2-migration` branch carries Phase 1 (hard cut)
  + Phase 2 (substrate) + Phase 3 (provider-server) of the Go
  rebuild; Issuer + receiver phases follow.

[0.5.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.5.0

## [0.4.3] — 2026-05-28

### Fixed

- LLM agents routinely called `social_send` with a top-level `message=`
  (or `body=`) kwarg instead of the canonical
  `payload={"text": "..."}` shape, and the cloud rejected the call with
  a Pydantic `extra_forbidden` error. The tool description now spells
  out the required arg shape inline (including a concrete example),
  giving the agent the canonical pattern at tool-discovery time.
- `social_respond` got the same treatment for the same reason — the
  agent was inventing a `message=` field for coordination responses.

[0.4.3]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.4.3

## [0.4.2] — 2026-05-27

### Fixed

- `mcp.tools.InboxWaitEvent.event_id` and
  `mcp.tools.InboxWaitOutput.next_event_id` had incorrect Pydantic
  `alias="eventId"` / `alias="nextEventId"` annotations that produced a
  non-spec wire format. The normative
  [`schemas/tools/social-inbox-wait-result.schema.json`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/schemas/tools/social-inbox-wait-result.schema.json)
  requires both fields to be snake_case on the wire (only `occurredAt` is
  camelCase). The aliases are removed; the wire now matches the spec
  (`event_id`, `next_event_id`).

  **Wire-breaking for sidecars that were relying on the wrong aliases.**
  Sidecars using `InboxWaitEvent.model_validate({"eventId": ...})` to
  build events MUST switch to spec-compliant snake_case keys
  (`{"event_id": ...}`) or keyword construction
  (`InboxWaitEvent(event_id=..., ...)`).

  Cross-impl conformance (`conformance/`) was already asserting against
  the snake_case schema; this brings the SDK back into alignment.

## [0.4.1] — 2026-05-27

### Added

- `ShadownetMCPSession` accepts an optional `mcp_endpoint` constructor
``  kwarg. When supplied, it overrides the synthesized
  `{base_url}/u/{shadowname}/mcp` URL. Sidecars MAY serve MCP from a
  different host than the dashboard (e.g., `api.example.org` for MCP vs
  `app.example.org` for the integration-bundle endpoint); the bundle's
``  `mcp_endpoint` field is the canonical source per RFC-0007 amendment A.
  Callers fetching the bundle SHOULD pass
  `mcp_endpoint=bundle.mcp_endpoint`. Existing callers passing only
  `base_url + shadowname` continue to work unchanged.

## [0.4.0] — 2026-05-25

### Removed

- Removed `shadownet.webhook` subpackage and the `social_set_webhook` MCP
  tool registration in `register_shadownet_tools`. Webhook delivery has
  been removed from the protocol (see shadownet-specs); receivers use
  `social_inbox_wait` (RFC-0007 amendment D) instead.
- Removed `social_set_webhook` from the `Sidecar` Protocol in
  `shadownet.mcp.protocol`, and `SetWebhookInput`/`SetWebhookOutput`
  from the public `shadownet.mcp` surface. Implementations no longer
  need to provide a `social_set_webhook` coroutine.

### Changed

- `ShadownetMCPSession.wait_inbox` now omits `last_event_id` from the
  tool-call kwargs when it is `None`, instead of sending an explicit
  `null`. Avoids spurious rejections on sidecars that validate the
  field strictly.
- `_extract_structured` transparently unwraps the MCP 1.27+
  `{"result": "<json-string>"}` envelope that newer MCP server SDKs
  emit for string tool returns, so callers continue to see the
  underlying tool-output dict.

### Fixed

- `_extract_structured` now surfaces `CallToolResult.isError=true` as a
  descriptive `MCPSessionError` instead of falling through to a generic
  "no structured content" error. Empty or unparseable text blocks also
  raise with the offending payload truncated into the message.
- Internal lint cleanup (`B904` exception chaining, `N806` variable
  casing) — no behavior change.

## [0.3.2] — 2026-05-22

### Added

- New error subclass `shadownet.sns.ShadownameExpired` (extends
  `ShadownameInvalid`) for SNS records whose `exp` is in the past but
  whose envelope is otherwise well-formed and validly signed. Callers
  that catch `ShadownameInvalid` continue to work unchanged; callers
  treating expiry as transient/re-resolvable can branch on the
  subclass.
- New module `shadownet.sns.renewal` with helpers
  (`due_at`, `is_due`, `renew_due`) for client-side SNS record
  re-registration before expiry, per RFC-0005 §Lifecycle. The renewer
  takes a caller-supplied `register` coroutine — the SDK does not
  expose a write-side SNS API at v0.1.

### Changed

- `shadownet.sns.record.verify_record` now checks `exp` against the
  `now` argument *before* JWT signature verification. Behavior with
  no `now` argument is unchanged (still uses `int(time.time())`);
  callers passing `now` for tests get deterministic expiry semantics
  and a distinct `ShadownameExpired` exception type.

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
    names, version).
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
  — SERVER side helpers for sidecar implementations (shadownet-local,
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
  API.

[0.3.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.3.0

## [0.2.1] — 2026-05-11

### Removed (superseded)

- `webhook.build_webhook_headers` and related webhook helpers — removed
  along with the entire `shadownet.webhook` subpackage in [Unreleased].

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
- **MCP** — Pydantic input/output models for every RFC-0007 tool, a
  `Sidecar` Protocol, and `register_shadownet_tools(server, sidecar)` to
  wire them onto a `FastMCP` instance.
- Fully `mypy --strict`-clean; ships `py.typed`; ruff lint+format clean;
  176 tests at the cut.

[Unreleased]: https://github.com/shadownet-protocol/shadownet/compare/python-sdk/v0.4.2...HEAD
[0.4.2]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.4.2
[0.4.1]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.4.1
[0.4.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.4.0
[0.3.2]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.3.2
[0.2.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/python-sdk%2Fv0.2.0
[0.1.3]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.0-rc.2...v0.1.1
[0.1.0rc2]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.0-rc.1...v0.1.0-rc.2
[0.1.0rc1]: https://github.com/shadownet-protocol/shadownet-py/releases/tag/v0.1.0-rc.1
