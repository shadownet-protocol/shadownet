# shadownet-go

Go SDK and reference server binaries for the [Shadownet](../shadownet-specs/) protocol.

## Status

v0.1 protocol implementation: SDK, reference SCA/SNS servers, and CLI. Implements the v0.1 RFCs at [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/).

## Requirements

- **Go 1.25+** to build from source or to import any `pkg/*` package as a library. The floor is set by `golang.org/x/sys` v0.42.0, transitively pulled in by `modernc.org/sqlite` (the pure-Go SQLite driver the reference servers use). Pre-built binaries from GitHub Releases have no Go runtime dependency.

## What this repo is

Two things in one Go workspace:

- **SDK** — reusable libraries for any Go program that needs to speak Shadownet (resolve a Shadowname, mint or verify a Verifiable Presentation, run an A2A handshake, build an SCA or SNS server).
- **Reference servers** — binaries that consume the SDK and implement the canonical SCA, SNS, and CLI defined by the spec.

It is not "the" Shadownet implementation. It is one of several language SDKs (alongside `shadownet-py` and `shadownet-ts`); interop is verified by [`shadownet-conformance`](../shadownet-specs/DEVELOPMENT.md).

## Planned layout

```
pkg/                   public, importable
  crypto/              Ed25519, JWS sign/verify
  did/                 did:key, did:web
  vc/                  VC-JWT issuance + verification + BitstringStatusList
  a2a/                 A2A client + server helpers
  sca/                 SCA library (issuance flow, ProofMethod + Store interfaces, predicate eval)
  sns/                 SNS library (record signing, resolution, Store interface)
cmd/
  sca-server/          reference SCA HTTP server
  sns-server/          reference SNS HTTP server
  shadownet/           CLI
internal/
  storesqlite/         SQLite-backed Store impls used by the reference servers
  storemem/            in-memory Store impls for tests and dev
api/                   OpenAPI / JSON Schema mirrors of the RFC endpoints
```

Storage interfaces live in `pkg/sca` and `pkg/sns`; the reference servers ship in-memory and SQLite (`modernc.org/sqlite`, CGo-free) implementations. Operators that need other backends provide their own `Store` implementations in their deployment repo.

Proof-method implementations are likewise out of `pkg/`: `pkg/sca` defines the `ProofMethod` interface, and `cmd/sca-server` ships a single `InstantApprovalProofMethod` for local development. SMTP, Stripe Identity, biometric kiosks, and similar live in operator deployments.

> **`InstantApprovalProofMethod` is for local development only.** Every `/proof/start` it sees opens a session that is immediately ready, so any `/issuance` request gets a credential. `cmd/sca-server` refuses to start when this method is configured against a non-loopback listener unless `SHADOWNET_ALLOW_INSTANT_APPROVAL=1` is set explicitly. Production deployments write their own `ProofMethod`.

## Distribution

Tagged releases (`v0.1.x` while the spec is at v0.1) publish:

- **Go module** — auto-indexed at [pkg.go.dev/github.com/shadownet-protocol/shadownet-go](https://pkg.go.dev/github.com/shadownet-protocol/shadownet-go) on tag.
- **Container images** — `ghcr.io/shadownet-protocol/sca-server:<tag>` and `ghcr.io/shadownet-protocol/sns-server:<tag>` (linux/amd64 + linux/arm64); `:latest` tracks the highest released non-pre-release tag.
- **CLI binaries** — `shadownet_<tag>_<os>_<arch>.tar.gz` plus `SHA256SUMS` attached to the GitHub Release (linux + macOS, amd64 + arm64).
- **OpenAPI specs** — `api/{sca,sns}/openapi.yaml` and `api/messages/envelope.schema.json` ship with the source; the canonical mirror at `schemas.shadownet.example` lands once the domain is allocated.

## Specifications

- Protocol: [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](../shadownet-specs/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](../shadownet-specs/DEVELOPMENT.md)

## License

MIT. See [`LICENSE`](./LICENSE).
