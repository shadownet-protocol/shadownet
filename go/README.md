# Shadownet — Go SDK

Go SDK and reference server binaries for the [Shadownet protocol](https://github.com/shadownet-protocol/shadownet-specs).

> **Note (`v0.2.0` — module path change).** This SDK was previously published as `github.com/shadownet-protocol/shadownet-go`. Starting with `v0.2.0` it is published as `github.com/shadownet-protocol/shadownet/go` from the [`shadownet-protocol/shadownet`](https://github.com/shadownet-protocol/shadownet) monorepo. See [`MIGRATION.md`](../../MIGRATION.md) for the full set of changes for existing consumers; the older `v0.1.x` releases stay reachable on the previous repo.

## Status

v0.1 protocol implementation: SDK, reference SCA + SNS servers, and CLI. Implements [RFC-0001 through RFC-0006](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs). RFC-0007 (MCP) is intentionally out of scope — that's the Sidecar surface, owned by the Python implementation in `hermes-social`.

## What this is

Two Go modules in this directory:

- **`github.com/shadownet-protocol/shadownet/go`** — the SDK (`pkg/*`) plus reference SCA + SNS server binaries (`cmd/*-server`) and the operator CLI (`cmd/shadownet`). Memory + SQLite storage drivers; zero pgx in the dependency graph.
- **`github.com/shadownet-protocol/shadownet/go/pgstore`** — a separate submodule that adds the Postgres backend. Operators who want PG depend on it explicitly; everyone else stays clean of `github.com/jackc/pgx/v5`.

It is not "the" Shadownet implementation — it is one of several language SDKs. The Python SDK lives at [`../py/`](../py/) in this same repo; cross-implementation interop is verified by [`shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-conformance).

## Quickstart

### As an operator — run the reference servers

The published container images and a sample [`docker-compose.yml`](./deploy/docker-compose.yml) bring an SCA and SNS up on loopback in one command:

```sh
go install github.com/shadownet-protocol/shadownet/go/cmd/shadownet@latest
cd deploy
shadownet keygen --out ./sca-issuer.jwk
shadownet keygen --out ./sns-provider.jwk
docker compose up

# In another shell:
curl http://127.0.0.1:8443/.well-known/sca/policy.json
curl 'http://127.0.0.1:8444/.well-known/sns/v1/resolve?name=alice@sh4dow.org'
```

The compose stack uses `InstantApprovalProofMethod` (see warning below) and binds only to `127.0.0.1` of the host. For production, write your own `ProofMethod`, terminate TLS in front of the binaries (or set `tls.cert`/`tls.key` in their YAML), and put the servers on real `did:web` DIDs.

### As a Go developer — import the SDK

```go
import (
    "github.com/shadownet-protocol/shadownet/go/pkg/crypto"
    "github.com/shadownet-protocol/shadownet/go/pkg/did"
    "github.com/shadownet-protocol/shadownet/go/pkg/vc"
    "github.com/shadownet-protocol/shadownet/go/pkg/a2a"
)
```

API documentation: [pkg.go.dev/github.com/shadownet-protocol/shadownet/go](https://pkg.go.dev/github.com/shadownet-protocol/shadownet/go).

### CLI

```sh
shadownet keygen --out ./holder.jwk        # Ed25519 keypair → did:key
shadownet resolve alice@sh4dow.org  # SNS lookup + verify
shadownet inspect <jwt>                    # decode VC / VP / freshness / SNS record
shadownet handshake --key holder.jwk --vc cred.jwt --peer-did <DID> https://peer/a2a
shadownet doctor --sca https://sca.example --sns https://sns.example
```

## Requirements

- **Go 1.25+** to build from source or to import any `pkg/*` package as a library. The floor is set by `golang.org/x/sys` v0.42.0, transitively pulled in by `modernc.org/sqlite` (the pure-Go SQLite driver the reference servers use). Pre-built binaries from GitHub Releases have no Go runtime dependency.

## Layout

```
go/                               # main module — no pgx
├── pkg/                               # public, importable; semver-stable
│   ├── crypto/                        Ed25519, JWS sign/verify (EdDSA only via go-jose v4)
│   ├── did/                           did:key, did:web (TLS 1.3, 16 KiB cap, Cache-Control)
│   ├── vc/                            VC-JWT, VP, freshness proof, status list, predicate eval, trust store
│   ├── a2a/                           A2A v1.0 surface + handshake; VP cache eviction
│   ├── sca/                           SCA library; ProofMethod + Store interfaces, callback HMAC delivery
│   │   └── storetest/                 reusable contract test suite for sca.*Store
│   ├── sns/                           SNS library; signed records, caching resolver, sanitized errors
│   │   └── storetest/                 reusable contract test suite for sns.RecordStore
│   ├── scaserver/                     SCA HTTP-server bootstrap; InstantApprovalProofMethod (dev-only)
│   ├── snsserver/                     SNS HTTP-server bootstrap
│   ├── httpx/                         hardened http.Server defaults, request-id, recover, access-log, IsLoopback
│   ├── keyguard/                      fixture-key safety net
│   ├── storemem/                      in-memory Store impls
│   └── storesqlite/                   SQLite Store impls (modernc.org/sqlite, CGo-free)
├── cmd/
│   ├── sca-server/                    default SCA binary (memory + sqlite drivers)
│   ├── sns-server/                    default SNS binary (memory + sqlite drivers)
│   └── shadownet/                     operator + developer CLI
├── internal/
│   ├── cli/                           CLI command implementations
│   └── config/                        YAML + env-var config loader
├── api/                               OpenAPI 3.1 + JSON-Schema mirrors of the RFC endpoints
├── build/                             Dockerfiles for all four reference binaries
└── deploy/                            sample docker-compose stack + YAML configs

pgstore/                               # github.com/shadownet-protocol/shadownet/go/pgstore
├── go.mod                             depends on parent + jackc/pgx/v5
├── schema.sql + sca.go + sns.go       Postgres-backed Store impls
└── cmd/{sca,sns}-server/              -pg binary variants (memory + sqlite + postgres drivers)
```

Storage interfaces live in `pkg/sca` and `pkg/sns`. Three SDK-shipped implementations: `pkg/storemem`, `pkg/storesqlite`, and `pgstore` (separate module). Operators wanting a fourth backend implement the same interfaces and validate via `pkg/{sca,sns}/storetest` — see [Custom storage](#custom-storage) below.

Proof methods are likewise out of `pkg/sca`: the package defines the `ProofMethod` interface and `pkg/scaserver` ships a single `InstantApprovalProofMethod` for local development. SMTP, Stripe Identity, biometric kiosks, and similar live in operator deployments.

> **`InstantApprovalProofMethod` is for local development only.** Every `/proof/start` it sees opens a session that is immediately ready, so any `/issuance` request gets a credential. `cmd/sca-server` refuses to start when this method is configured against a non-loopback listener unless `SHADOWNET_ALLOW_INSTANT_APPROVAL=1` is set explicitly. Production deployments write their own `ProofMethod`.

## Operational notes

Both reference servers expose:

- `GET /healthz` and `GET /livez` — always 200 while the process is alive.
- `GET /readyz` — 200 when the backing store is reachable, 503 otherwise (sqlite driver pings the DB).
- Structured `log/slog` output. Container images set `SHADOWNET_LOG_FORMAT=json` by default; override with `text` for local runs.
- Graceful shutdown on `SIGTERM`/`SIGINT`: in-flight requests drain (15 s deadline), then the SQLite handle is closed.

Configuration is YAML with `SHADOWNET_<SECTION>_<KEY>` env-var overrides. See [`deploy/sca-server.yaml`](./deploy/sca-server.yaml) and [`deploy/sns-server.yaml`](./deploy/sns-server.yaml) for working examples.

## Distribution

Tagged releases publish:

- **Go modules** — both auto-indexed at pkg.go.dev on tag. Monorepo tag scheme: the main module is tagged `go/vX.Y.Z`, and the pgstore submodule is tagged `go/pgstore/vX.Y.Z` (Go requires the directory prefix on tags from sub-module subtrees).
  - [`github.com/shadownet-protocol/shadownet/go`](https://pkg.go.dev/github.com/shadownet-protocol/shadownet/go) — SDK + default binaries.
  - [`github.com/shadownet-protocol/shadownet/go/pgstore`](https://pkg.go.dev/github.com/shadownet-protocol/shadownet/go/pgstore) — Postgres backend.
- **Container images** (linux/amd64 + linux/arm64; `:latest` tracks the highest non-pre-release tag):
  - `ghcr.io/shadownet-protocol/sca-server:<tag>` — SCA, memory + sqlite drivers. Self-host default.
  - `ghcr.io/shadownet-protocol/sns-server:<tag>` — SNS, memory + sqlite drivers. Self-host default.
  - `ghcr.io/shadownet-protocol/sca-server-pg:<tag>` — SCA, memory + sqlite + postgres drivers. Cloud-tier default.
  - `ghcr.io/shadownet-protocol/sns-server-pg:<tag>` — SNS, memory + sqlite + postgres drivers. Cloud-tier default.
- **CLI binaries** — `shadownet_<tag>_<os>_<arch>.tar.gz` plus `SHA256SUMS` attached to the GitHub Release (linux + macOS, amd64 + arm64).
- **OpenAPI specs** — [`api/{sca,sns}/openapi.yaml`](./api/) and [`api/messages/envelope.schema.json`](./api/messages/envelope.schema.json) ship with the source; the canonical mirror at `schemas.sh4dow.org` lands once the domain is allocated.

Use the default `:sca-server` / `:sns-server` images unless you need Postgres. The `-pg` variants exist for operators with managed Postgres (RDS, Cloud SQL, Aurora) or HA via streaming replication.

## Custom storage

The default binaries ship three storage drivers (memory, sqlite, postgres). Operators that need a fourth backend (DynamoDB, Cassandra, MySQL, …) implement the `Store` interfaces in `pkg/sca` and `pkg/sns` and ship their own binary. The path:

1. Implement `sca.SessionStore`, `sca.IssuanceStore`, `sca.RevocationStore`, and `sns.RecordStore` against your backend.
2. Validate via the contract suites in [`pkg/sca/storetest`](./pkg/sca/storetest) and [`pkg/sns/storetest`](./pkg/sns/storetest) — the same suites that validate `pkg/storemem`, `pkg/storesqlite`, and `pgstore`. Passing them is the protocol-conformance bar for storage.
3. Wire your stores into `pkg/scaserver.Run` / `pkg/snsserver.Run`. The reference binaries are ~150 LOC of YAML loading + driver selection on top of those entry points; your binary follows the same shape.

[`pgstore/`](./pgstore) is the canonical worked example — a separate Go submodule that adds Postgres without polluting the main module's dependency graph.

## Operational caveats

What the reference binaries deliberately do **not** include — these belong to the deployment, not the binary:

- **Rate limiting / abuse mitigation** — terminate at a reverse proxy or WAF in front of the binary. The HTTP server enforces hard timeouts (read-header 5s, read 10s, write 30s, idle 120s) but does no per-IP throttling.
- **Metrics, tracing, profiling endpoints** — operator-supplied. Wrap the `http.Handler` returned by `pkg/scaserver` / `pkg/snsserver` with your OTel / Prometheus middleware, or run a sidecar that scrapes structured `slog` output.
- **Multi-region / HA** — infrastructure layer. With the `-pg` images: PG read replicas + LB + multiple binary replicas. SQLite-backed binaries are single-node by design.
- **Schema migrations beyond the v0.1 baseline** — `pgstore` applies its schema once on startup. Future schema changes will ship a migrations table and tooling; v0.1.x is one schema only.

Teams that need any of these as part of the binary itself should fork `cmd/{sca,sns}-server` (or `pgstore/cmd/{sca,sns}-server`) and wire what they need on top of `pkg/scaserver.Run` / `pkg/snsserver.Run`.

## Specifications

- Protocol RFCs: [shadownet-specs/rfcs](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs)
- Wire-level walkthrough: [shadownet-specs/examples/birthday-flow.md](https://github.com/shadownet-protocol/shadownet-specs/blob/main/examples/birthday-flow.md)
- Development plan: [shadownet-specs/DEVELOPMENT.md](https://github.com/shadownet-protocol/shadownet-specs/blob/main/DEVELOPMENT.md)

## Contributing

See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) at the repo root for the full contributor guide. Quick gate before sending a PR (run from this directory):

```sh
go test -race -count=1 ./...
go vet ./...
gofumpt -l -extra .   # must print nothing
staticcheck ./...
golangci-lint run     # uses .golangci.yml; v2.12+ recommended
govulncheck ./...
./tools/spdx-check.sh
./tools/check-schemas.sh
```

## License

MIT. See [`LICENSE`](./LICENSE).
