# Changelog

All notable changes to the Shadownet reference Go binaries are documented
here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows semantic versioning (`vMAJOR.MINOR.PATCH`). Pre-1.0,
breaking changes land in minor bumps.

The `pgstore` submodule is versioned in lockstep with the main module:
historically tagged `pgstore/vX.Y.Z`; in the monorepo it is tagged
`core/pgstore/vX.Y.Z` to satisfy Go's directory-prefix tag requirement for
sub-module subtrees. The main module is tagged `core/vX.Y.Z`.

## [Unreleased]

### Changed — v0.2 protocol rebuild

`core/` is now **two Go reference HTTP server binaries** plus the Postgres
backend submodule. **There is no public Go SDK.** The canonical client SDK
is `python-sdk` (PyPI: `shadownet`).

- `cmd/provider-server` — multi-tenant Shadowname host (RFC 0001 §5.2).
  Serves signed A2A AgentCards at `<ep>/identity/<local>` with ETag /
  conditional-refresh handling. Subcommands: `serve`, `dns-record`,
  `admin {add, remove, list}`. Default SQLite store + optional Postgres
  via the `core/pgstore` submodule.
- `cmd/issuer-server` — `org_affiliation` credential issuer + per-epoch
  revocation bitstring (RFC 0001 §6.4–§6.5). Supports both addressing
  modes:
  - *domain mode* — routes at `/.well-known/shadownet/issue` +
    `/.well-known/shadownet/status/{epoch}`; §6.6 authorization covers
    same-domain, sub-domain, and DNS-delegate paths.
  - *keyed mode* — self-serves an AgentCard at
    `/.well-known/agent-card.json` declaring `shadownet:issueEndpoint` +
    `shadownet:statusListBase`, with §6.6 collapsed to `iss == org`.
  Subcommands: `serve`, `admin {approve, reject, revoke, rotate-epoch,
  list-pending}`. Default ceremony driver is `queue` (store-backed,
  admin CLI drives state); `dev` driver auto-approves and refuses to
  start on non-loopback listeners unless `SHADOWNET_ALLOW_AUTO_APPROVE=1`.
- New in-tree primitives under `internal/`: RFC 8785 JCS canonicalizer
  (cross-corpus tested against python-sdk's `shadownet.jcs`), Domain ∣
  Shadowname ∣ MultibasePubKey identifier union, A2A §8.4 AgentCard
  build + JWS sign + verify, `shadownet-cred+jwt` and `shadownet-csr+jwt`
  mint + verify, gzip+base64url big-endian-within-byte bitstring.
- Removed: `pkg/{a2a,did,sca,scaserver,sns,snsserver,vc,storemem,storesqlite}`,
  `cmd/{sca-server,sns-server,shadownet}`, `api/{sca,sns,messages}`,
  `deploy/{sca,sns}-server.yaml`, `build/Dockerfile.{sca,sns}-*`,
  `internal/cli/{resolve,handshake,doctor}.go`, the legacy
  `pgstore/{sca,sns,schema}.{go,sql}` and `pgstore/cmd/{sca,sns}-server`.
  `pkg/` is now empty — everything is `internal/`.
- `core/pgstore` rewritten: `ProviderStore` + `IssuerStore` over pgx/v5,
  with `AllocateIndex` using `SELECT ... FOR UPDATE` for concurrent-safe
  index assignment. Schema applied under `pg_advisory_xact_lock` so
  concurrent boots don't race on DDL.
- New deploy artefacts: `deploy/{provider,issuer}-server.yaml` (annotated
  example configs), `deploy/docker-compose.yml`, and
  `build/Dockerfile.{provider,issuer}-server`.
- GHCR image rename:
  `sca-server` → `issuer-server`, `sns-server` → `provider-server`
  (`-pg` variants follow). The v0.1 image paths remain available at
  their old names — pin explicitly if you operate the v0.1 servers.
- The v0.1 Go module versions (`core/v0.2.x` on the Go proxy) remain
  reachable for anyone pinned to them; the first v0.2-protocol release
  will be tagged `core/v0.3.0`.

## [v0.2.1] — 2026-05-22

### Changed

- `pkg/sns`: `Server.DefaultTTL` fallback bumped from 300s to 3600s, and
  `cmd/sns-server` config default raised in lockstep. Aligns the
  reference SNS server with the cross-implementation recommended
  default for RFC-0005 §Lifecycle. Note this is a cache-hint change
  only for the reference server, which re-issues the JWT envelope with
  a fresh `iat` on every resolve (`pkg/sns.serveResolve` calls
  `IssueRecord` with `s.now()`), so existing records never go stale at
  the wire boundary. Operators relying on the old default can pin it
  explicitly via the `defaultTtl` config field or by setting
  `Server.DefaultTTL = 300` on the embedded SDK.

## [v0.2.0] — 2026-05-10

### Changed

- **BREAKING:** the project moved from
  [`shadownet-protocol/shadownet-go`](https://github.com/shadownet-protocol/shadownet-go)
  into the [`shadownet-protocol/shadownet`](https://github.com/shadownet-protocol/shadownet)
  monorepo at `core/`. The Go module path is now
  `github.com/shadownet-protocol/shadownet/core`; the `pgstore` submodule
  path is now `github.com/shadownet-protocol/shadownet/core/pgstore`.
  Update imports accordingly. The `v0.1.x` releases on the previous repo
  remain reachable to existing consumers via the old import path. See
  [`MIGRATION.md`](../../MIGRATION.md) for the full migration notes.
- Tag scheme in the monorepo: main module is tagged `core/vX.Y.Z`,
  pgstore submodule is tagged `core/pgstore/vX.Y.Z`.

[v0.2.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/core%2Fv0.2.0

## [v0.1.7] — 2026-05-09

### Fixed

- `pgstore.Open` no longer races when two backends boot concurrently against
  the same database. The schema-apply step is now wrapped in a transaction
  guarded by `pg_advisory_xact_lock`, so simultaneous `sca-server-pg` /
  `sns-server-pg` startups against a shared Postgres serialize on the lock
  instead of crashing one with `duplicate key value violates unique
  constraint "pg_type_typname_nsp_index"`. `CREATE TABLE IF NOT EXISTS` is
  not a synchronization primitive in Postgres; this is the canonical fix.
  Reported from a real co-tenant deployment. Integration test exercises
  the race against a real Postgres.

[v0.1.7]: https://github.com/shadownet-protocol/shadownet-go/releases/tag/v0.1.7

## [v0.1.6] — 2026-05-09

The first usable release of the production-readiness pass. v0.1.4 and
v0.1.5 were cut but their `-pg` Docker images failed to build:

- v0.1.4 — `COPY go.work` against a `go.work` that's correctly gitignored.
- v0.1.5 — `go build ./pgstore/cmd/sca-server` from the parent module's
  context, which can't address packages in a sibling module.

v0.1.6 fixes the Dockerfiles (build from inside `pgstore/`) and ships the
full image set, plus a concurrency fix in `pgstore.AssignIndex` that
eliminated `FOR UPDATE SKIP LOCKED`-induced false rotations under
contention.

[v0.1.6]: https://github.com/shadownet-protocol/shadownet-go/releases/tag/v0.1.6

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

[v0.1.3]: https://github.com/shadownet-protocol/shadownet-go/releases/tag/v0.1.3
