# Changelog

All notable changes to `shadownet-go` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows semantic versioning (`vMAJOR.MINOR.PATCH`). Pre-1.0, breaking changes
land in minor bumps.

The `pgstore` submodule is versioned in lockstep with the main module
(`pgstore/vX.Y.Z`).

## [v0.1.5] — 2026-05-09

The first usable release of the production-readiness pass; the v0.1.4 tag
was cut but its `-pg` Docker images failed to build (a `COPY go.work` line
that hadn't been gitignored). v0.1.5 fixes the Dockerfiles and ships the
full image set.

### Added

- New **`pgstore/`** Go submodule providing Postgres-backed storage —
  `sca.SessionStore`, `sca.IssuanceStore`, `sca.RevocationStore`,
  `sns.RecordStore`. Separate module so the main module's go.sum stays free of
  `github.com/jackc/pgx/v5`.
- New reference binaries `sca-server-pg` and `sns-server-pg` (memory + sqlite +
  postgres drivers) and matching distroless container images
  (`ghcr.io/shadownet-protocol/{sca,sns}-server-pg`).
- `pkg/sca/storetest` and `pkg/sns/storetest` — reusable contract test suites
  that any `Store` implementation can drive. Passing them is the conformance
  bar for third-party storage backends.
- `pkg/scaserver` and `pkg/snsserver` — HTTP-server bootstrap packages
  (`Run(ctx, RunConfig) error`). All four reference binaries now share one
  hardened entry point.
- `pkg/scaserver.InstantApprovalProofMethod` and
  `AssertInstantApprovalNotPublic` — moved out of the `sca-server` binary so
  any custom binary built off `pkg/sca` can opt in.
- `pkg/sca` callback dispatch — HMAC-SHA256-signed POST per RFC-0004
  §Callbacks, fired on session `ready` / `failed` / `expired` transitions.
  `Caller` interface, `HTTPCaller` implementation with the RFC retry schedule
  (`0, 5s, 30s, 5min, 30min`), and `VerifyCallbackSignature` helper for
  receivers.
- `pkg/a2a.Server.SweepVPCache` — explicit sweep API; automatic eviction of
  expired VP cache entries on writes.
- Status-list rotation in revocation stores. `AssignIndex` transparently
  allocates a new list (`<base>-N`) when the active list reaches capacity;
  issuance no longer halts at 131,072 credentials.
- `CHANGELOG.md` (this file).

### Changed

- Promoted from `internal/` to `pkg/`: `pkg/httpx`, `pkg/keyguard`,
  `pkg/storemem`, `pkg/storesqlite`. All four are now importable by operators
  wiring custom binaries.
- `pkg/a2a` envelope `interaction` field is now optional, matching the
  upstream RFC-0006 schema revision.
- SNS handler error responses no longer leak internal error text
  (`"sql: no rows"`, file paths, driver messages). Full errors are logged via
  `slog`; response bodies return sanitized strings.

### Fixed

- VP cache in `pkg/a2a` no longer grows unbounded under steady-state
  operation.

### Operations

- CI now exercises both modules — build, tests, vet, lint, `govulncheck`.
  New `integration` job runs the `pgstore` test suite against a `postgres:16`
  service container.
- Release pipeline image matrix expanded from 2 to 4 entries
  (`{sca,sns}-server` + `{sca,sns}-server-pg`).
- Bumped `gofumpt` to `v0.10.0`; logging format aligned across packages.
- Distroless images pre-create `/var/lib/shadownet` with `nonroot` ownership
  and declare it as a volume.

## [v0.1.3] — earlier

CI / logging polish: aligned multiline log statements; bumped CI's `gofumpt`
to `v0.10.0`. Pre-created `/var/lib/shadownet` in distroless images with
correct ownership and declared it as a volume.

## [v0.1.2] and earlier

Initial v0.1 protocol implementation (RFC-0001 through RFC-0006): SDK
(`pkg/{crypto,did,vc,a2a,sca,sns}`), reference SCA + SNS servers, operator
CLI, build-time version stamping, fixture-key safety net (`keyguard`).

[v0.1.5]: https://github.com/shadownet-protocol/shadownet-go/releases/tag/v0.1.5
[v0.1.3]: https://github.com/shadownet-protocol/shadownet-go/releases/tag/v0.1.3
