# shadownet-go

Go SDK and reference server binaries for the [Shadownet](../shadownet-specs/) protocol.

## Status

Early. No code yet. Implements the v0.1 RFCs at [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/).

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

The directory tree is not committed yet — added incrementally as work lands.

## Specifications

- Protocol: [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](../shadownet-specs/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](../shadownet-specs/DEVELOPMENT.md)

## License

MIT. See [`LICENSE`](./LICENSE).
