# shadownet-go

Go SDK and reference server binaries for the [Shadownet protocol](https://github.com/shadownet-protocol/shadownet-specs).

## Status

v0.1 protocol implementation: SDK, reference SCA + SNS servers, and CLI. Implements [RFC-0001 through RFC-0006](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs). RFC-0007 (MCP) is intentionally out of scope — that's the Sidecar surface, owned by the Python implementation in `hermes-social`.

## What this repo is

Two things in one Go module:

- **SDK** — reusable libraries for any Go program that needs to speak Shadownet (resolve a Shadowname, mint or verify a Verifiable Presentation, run an A2A handshake, build an SCA or SNS server).
- **Reference servers** — single-binary HTTP servers that consume the SDK and implement the canonical SCA and SNS defined by the spec, plus an operator CLI.

It is not "the" Shadownet implementation. It is one of several language SDKs (alongside `shadownet-py` and `shadownet-ts`); cross-implementation interop is verified by [`shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/DEVELOPMENT.md).

## Quickstart

### As an operator — run the reference servers

The published container images and a sample [`docker-compose.yml`](./deploy/docker-compose.yml) bring an SCA and SNS up on loopback in one command:

```sh
go install github.com/shadownet-protocol/shadownet-go/cmd/shadownet@latest
cd deploy
shadownet keygen --out ./sca-issuer.jwk
shadownet keygen --out ./sns-provider.jwk
docker compose up

# In another shell:
curl http://127.0.0.1:8443/.well-known/sca/policy.json
curl 'http://127.0.0.1:8444/.well-known/sns/v1/resolve?name=alice@shadownet.example'
```

The compose stack uses `InstantApprovalProofMethod` (see warning below) and binds only to `127.0.0.1` of the host. For production, write your own `ProofMethod`, terminate TLS in front of the binaries (or set `tls.cert`/`tls.key` in their YAML), and put the servers on real `did:web` DIDs.

### As a Go developer — import the SDK

```go
import (
    "github.com/shadownet-protocol/shadownet-go/pkg/crypto"
    "github.com/shadownet-protocol/shadownet-go/pkg/did"
    "github.com/shadownet-protocol/shadownet-go/pkg/vc"
    "github.com/shadownet-protocol/shadownet-go/pkg/a2a"
)
```

API documentation: [pkg.go.dev/github.com/shadownet-protocol/shadownet-go](https://pkg.go.dev/github.com/shadownet-protocol/shadownet-go).

### CLI

```sh
shadownet keygen --out ./holder.jwk        # Ed25519 keypair → did:key
shadownet resolve alice@shadownet.example  # SNS lookup + verify
shadownet inspect <jwt>                    # decode VC / VP / freshness / SNS record
shadownet handshake --key holder.jwk --vc cred.jwt --peer-did <DID> https://peer/a2a
shadownet doctor --sca https://sca.example --sns https://sns.example
```

## Requirements

- **Go 1.25+** to build from source or to import any `pkg/*` package as a library. The floor is set by `golang.org/x/sys` v0.42.0, transitively pulled in by `modernc.org/sqlite` (the pure-Go SQLite driver the reference servers use). Pre-built binaries from GitHub Releases have no Go runtime dependency.

## Layout

```
pkg/                  public, importable; semver-stable
  crypto/             Ed25519, JWS sign/verify (EdDSA only via go-jose v4)
  did/                did:key, did:web (TLS 1.3, 16 KiB cap, Cache-Control-aware)
  vc/                 VC-JWT, VP, freshness proof, BitstringStatusList, predicate eval, trust store
  a2a/                A2A v1.0 surface (message:send, message:stream/SSE, task:get, task:cancel) + handshake
  sca/                SCA library: ProofMethod + Store interfaces, issuance pipeline, RFC-0004 endpoints
  sns/                SNS library: signed records, caching resolver, RFC-0005 endpoints
cmd/
  sca-server/         reference SCA HTTP server
  sns-server/         reference SNS HTTP server
  shadownet/          operator + developer CLI
internal/             not importable downstream
  storesqlite/        SQLite-backed Store impls (modernc.org/sqlite, CGo-free)
  storemem/           in-memory Store impls
  httpx/              hardened http.Server defaults, request-id, recover, access-log
  config/             YAML + env-var config loader
  cli/                CLI command implementations
api/                  OpenAPI 3.1 specs + JSON-Schema mirrors of the RFC endpoints
build/                Dockerfiles for the reference servers
deploy/               sample docker-compose stack and YAML configs
```

Storage interfaces live in `pkg/sca` and `pkg/sns`; concrete implementations live in `internal/store*` and are wired only by the `cmd/*-server` binaries. Operators that need other backends write their own `Store` implementations in their deployment repo.

Proof-method implementations are likewise out of `pkg/`: `pkg/sca` defines the `ProofMethod` interface and `cmd/sca-server` ships a single `InstantApprovalProofMethod` for local development. SMTP, Stripe Identity, biometric kiosks, and similar live in operator deployments.

> **`InstantApprovalProofMethod` is for local development only.** Every `/proof/start` it sees opens a session that is immediately ready, so any `/issuance` request gets a credential. `cmd/sca-server` refuses to start when this method is configured against a non-loopback listener unless `SHADOWNET_ALLOW_INSTANT_APPROVAL=1` is set explicitly. Production deployments write their own `ProofMethod`.

## Operational notes

Both reference servers expose:

- `GET /healthz` and `GET /livez` — always 200 while the process is alive.
- `GET /readyz` — 200 when the backing store is reachable, 503 otherwise (sqlite driver pings the DB).
- Structured `log/slog` output. Container images set `SHADOWNET_LOG_FORMAT=json` by default; override with `text` for local runs.
- Graceful shutdown on `SIGTERM`/`SIGINT`: in-flight requests drain (15 s deadline), then the SQLite handle is closed.

Configuration is YAML with `SHADOWNET_<SECTION>_<KEY>` env-var overrides. See [`deploy/sca-server.yaml`](./deploy/sca-server.yaml) and [`deploy/sns-server.yaml`](./deploy/sns-server.yaml) for working examples.

## Distribution

Tagged releases (`v0.1.x` while the spec is at v0.1) publish:

- **Go module** — auto-indexed at [pkg.go.dev/github.com/shadownet-protocol/shadownet-go](https://pkg.go.dev/github.com/shadownet-protocol/shadownet-go) on tag.
- **Container images** — `ghcr.io/shadownet-protocol/sca-server:<tag>` and `ghcr.io/shadownet-protocol/sns-server:<tag>` (linux/amd64 + linux/arm64); `:latest` tracks the highest released non-pre-release tag.
- **CLI binaries** — `shadownet_<tag>_<os>_<arch>.tar.gz` plus `SHA256SUMS` attached to the GitHub Release (linux + macOS, amd64 + arm64).
- **OpenAPI specs** — [`api/{sca,sns}/openapi.yaml`](./api/) and [`api/messages/envelope.schema.json`](./api/messages/envelope.schema.json) ship with the source; the canonical mirror at `schemas.shadownet.example` lands once the domain is allocated.

## Specifications

- Protocol RFCs: [shadownet-specs/rfcs](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs)
- Wire-level walkthrough: [shadownet-specs/examples/birthday-flow.md](https://github.com/shadownet-protocol/shadownet-specs/blob/main/examples/birthday-flow.md)
- Development plan: [shadownet-specs/DEVELOPMENT.md](https://github.com/shadownet-protocol/shadownet-specs/blob/main/DEVELOPMENT.md)

## Contributing

Development conventions live in [`CLAUDE.md`](./CLAUDE.md). Quick gate before sending a PR:

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
