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
  crypto/              Ed25519, JWT/JWS
  did/                 did:key, did:web
  vc/                  VC-JWT issuance + verification + BitstringStatusList
  a2a/                 A2A client + server helpers
  sca/                 SCA library (issuance flow, trust store, predicate eval)
  sns/                 SNS library (record signing, resolution)
cmd/
  sca-server/          reference SCA HTTP server
  sns-server/          reference SNS HTTP server
  shadownet/           CLI
internal/
  store/               pluggable storage (sqlite, postgres)
api/                   OpenAPI specs derived from the RFCs
```

The directory tree is not committed yet — added incrementally as work lands.

## Specifications

- Protocol: [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](../shadownet-specs/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](../shadownet-specs/DEVELOPMENT.md)

## License

TBD.
