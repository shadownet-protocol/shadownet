# Changelog

All notable changes to `shadownet-py` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [PEP 440](https://peps.python.org/pep-0440/) versioning. Releases
track the protocol version they implement; while the spec is at `v0.1` the
SDK ships as `0.1.x`.

## [Unreleased]

_Nothing yet — track changes here as they land on `main`._

## [0.1.3] — 2026-05-03

Placeholder release slot. Add changes here before tagging `v0.1.3`.

## [0.1.2] — 2026-05-03

Four interop bugs caught by `shadownet-conformance` against v0.1.1.

### Fixed

- `sca.csr.build_subject_auth` now sets `kid` in the JWT header per
  RFC-0004 §Common: subject authentication. Defaults to the bare holder DID;
  override via the new `kid=` keyword argument when a `did:web` controller
  has multiple verification methods.
- `sca.csr.build_csr` gets the same treatment — header now carries `kid`.
- `a2a.session.mint_session_token` mirrors the change for symmetry. RFC-0006
  doesn't strictly require `kid` on session tokens, but stricter peer SDKs
  may; this keeps holder-signed JWTs consistent across the surface.
- `sca.policy.LevelPolicy.method` and `sca.client.ProofSession.method`
  drop the over-strict `^urn:` regex per RFC-0004 §Policy document
  (`method` is an "operator-defined URI" — any URI scheme is valid).

### Tests

- 9 new regression tests in `tests/unit/test_v0_1_2_regressions.py`
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

[Unreleased]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.0-rc.2...v0.1.1
[0.1.0rc2]: https://github.com/shadownet-protocol/shadownet-py/compare/v0.1.0-rc.1...v0.1.0-rc.2
[0.1.0rc1]: https://github.com/shadownet-protocol/shadownet-py/releases/tag/v0.1.0-rc.1
