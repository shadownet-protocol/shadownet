# Changelog

All notable changes to this project follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The version mirrors the protocol version this suite tests. See
[CLAUDE.md](./CLAUDE.md) §Versioning & release.

## [Unreleased]

_Nothing yet._

## [0.1.1] — 2026-05-09

### Changed

- Resync bundled `_specs/` schemas to the upstream `shadownet-specs`
  state on 2026-05-04: `interaction` is now optional in the envelope
  schema; payload documents the `text`/`hints` shape for the free-form
  default; both schemas' `$id` use the canonical `sh4dow.org` host.
- Runtime SDK pin moved to `shadownet>=0.1.3` (RFC-0004 `kid` fix in
  CSR + subject-auth + session-token JWTs).

### Added

- Envelope conformance tests for the v0.1 free-form path (RFC-0006
  §Default form): minimal text-only envelope validates; verifier
  obligations covered (missing `interaction` MUST validate, unknown
  `interaction` MUST validate). Five new tests in
  `tests/conformance/test_envelope_schema.py`.

## [0.1.0] — 2026-05-04

### Added

- **Scaffold + CLI.** `shadownet-conformance` entry point with config via
  CLI flags + `SHADOWNET_CONFORMANCE_*` env vars. Three reports: JUnit XML,
  RFC-keyed JSON, GitHub Actions step-summary markdown.
- **Cross-SDK fixture safety net** (`shadownet-conformance-fixtures regen`).
  Versioned seed manifest at `fixtures/seeds.toml`; declarative entries at
  `fixtures/_regen/manifest.toml`. The regen CLI pipes each spec through
  the Python emitter (using `shadownet-py`) and the Go emitter (small Go
  binary at `fixtures/_regen/go-emit/` importing `shadownet-go`), byte-diffs
  the outputs, and refuses to write a fixture unless both SDKs produce
  identical bytes. Initial fixture set: 6 keypairs, 5 credentials, 3
  freshness proofs, 2 VPs, 1 SNS record, 1 BitstringStatusList VC.
- **Predicate evaluator tests** (RFC-0004 §Required-level predicates).
  Exhaustive table of leaf / `all` / `any` / `not` cases including the
  three RFC-0004 example predicates and the depth-4 limit.
- **JSON-Schema conformance tests** for the credential and envelope
  schemas in `../shadownet-specs/schemas/`.
- **SCA tests** (RFC-0004): well-known DID document, policy document,
  subject-auth (missing / wrong-aud / expired / TTL-cap), proof-flow
  (start / status / instant-approval ready / unknown-session),
  issuance (happy path / session consumed / bogus session), freshness
  (happy path / unknown jti), status list (reachable / shape /
  cache-control SHOULD), re-issuance (new jti / freshness still works).
- **SNS tests** (RFC-0005): well-known DID document, resolve happy path,
  404 for unknown shadowname, ttl bounds, signed-record verification
  end-to-end, alg = EdDSA, negative cache bound.
- **Sidecar tests** (RFC-0006): agent card, handshake (missing auth /
  missing VP → `presentation_required` / valid handshake / wrong-aud
  session / malformed VP / expired session / no-envelope-part / error
  envelope shape).
- **Round-trip e2e tests**. SCA round-trip (issue with A, verify with B)
  and SNS round-trip (resolve same name on A and B, assert agreement).
  Sidecar round-trip is registered but skipped pending a second v0.1
  implementation.
- **In-process A2A test peer.** Starlette/uvicorn ASGI server bound to a
  random localhost port. Hosts `/.well-known/agent-card.json`,
  `/a2a/{method}`, and `/webhooks/inbox`. Records inbound requests for
  test inspection; mints session tokens + VPs as the outbound side of a
  Sidecar handshake.
- **RFC-marker enforcement.** Tests under
  `tests/{predicate,sca,sns,sidecar,e2e}/` MUST carry
  `pytest.mark.rfc(number, section=..., requirement=...)` — collection
  fails with a clear list otherwise. Verified by `pytester`-driven tests.
- **GitHub Action.** `action/action.yml` + Docker image
  (`action/Dockerfile` → `ghcr.io/shadownet-protocol/conformance`).
- **CI workflows.** `ci.yml` (lint + mypy + pytest + fixture
  drift-check), `release.yml` (gate → multi-arch image push to ghcr.io →
  cut GitHub Release), `self-test.yml` (installs published `shadownet-go`
  binaries via the Go module proxy and runs the suite end-to-end on
  every PR).
- Runtime dep on `shadownet>=0.1.3` from PyPI for crypto / DID / VC /
  SCA / SNS / A2A / webhook primitives.
- **Distribution: Docker image + GitHub Action + `uvx`.** PyPI is
  intentionally not used at v0.1 — consumers run the Docker image
  directly, via the GitHub Action that wraps it, or via
  `uvx --from git+https://github.com/shadownet-protocol/shadownet-conformance@v0.1.0`.
- Development conventions in [`CLAUDE.md`](./CLAUDE.md).
