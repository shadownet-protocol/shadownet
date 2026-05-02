# shadownet-conformance

Wire-level interop test suite for the [Shadownet](../shadownet-specs/) protocol.

## Status

Early. No code yet. Tests the v0.1 RFCs at [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/).

## What this repo is

The canonical "is your implementation correct?" check. A `pytest`-based runner that takes a target URL — an SCA, SNS, or Sidecar — and exercises every spec'd endpoint, error code, and edge case from the RFCs. It does not care which language the implementation is written in; it only speaks the wire.

It is what catches **Go and Python disagreeing about JWT canonicalization**, or **the Python verifier rejecting a credential the Go SCA happily issued**, or **a Sidecar accepting a replayed VP nonce**. None of an implementation's own internal tests can catch these — only an external runner with canonical fixtures can.

## What it tests

- **SCA** — every RFC-0004 endpoint (`/proof/start`, `/proof/status`, `/issuance`, `/freshness`, `/status/<list-id>`, `/.well-known/{did,sca/policy}.json`), including negative cases (expired sessions, mismatched CSR/session, revoked credentials).
- **SNS** — RFC-0005 resolution, signed records, key rotation, cross-provider rejection.
- **Sidecar** — RFC-0006 A2A handshake (session token + VP exchange), error envelope, async task semantics; RFC-0007 MCP tool surface and webhook contract.
- **Round-trip** — issue with impl A, verify with impl B; sign a record with A, resolve with B.
- **Predicate evaluation** — given the same `(predicate, presentation)` pair, all impls must return the same boolean.

## How it runs

```bash
uv sync
uv run shadownet-conformance --target sca=https://sca.example  --target sns=https://sns.example  --target sidecar=https://shadow.example
```

Also published as a **GitHub Action** so any implementation's CI can verify itself before merging:

```yaml
- uses: shadownet-protocol/conformance-action@v0.1
  with:
    sca:     http://localhost:8443
    sns:     http://localhost:8444
    sidecar: http://localhost:8340
```

## Test vectors

Canonical fixtures live in `fixtures/` — deterministic Ed25519 keypairs (from fixed seeds), credential JWTs with byte-exact expected outputs, VPs, SNS records, status list credentials, predicate eval pairs, and malformed inputs paired with the expected error code. These let any implementation assert byte-for-byte equality, not just "looks roughly right."

The fixture set is the protocol's empirical contract. New normative behavior in a future RFC lands a fixture before it lands in code.

## Tooling

- **Package manager**: [`uv`](https://docs.astral.sh/uv/)
- **Python**: 3.12+
- **Test runner**: `pytest`
- **HTTP client**: `httpx` (async)
- **JWT / DID / VC primitives**: imported from [`shadownet-py`](../shadownet-py/)
  (the SDK is a runtime dependency; the safety against an SDK bug masking a
  wire bug is the cross-SDK fixture regen — see [`CLAUDE.md`](./CLAUDE.md)
  §Fixture discipline).

While `shadownet-py` is pre-release, it is consumed as an editable path
dependency declared in `pyproject.toml` (`[tool.uv.sources]`). The path
swap to a pinned PyPI version is mechanical and lands when the SDK
publishes its first release.

To run the **fixture regeneration** CLI you also need a Go toolchain
(Go 1.25+) and a checkout of [`shadownet-go`](../shadownet-go/). Running
the conformance suite itself never needs Go.

## Distribution

| Output | Where |
| --- | --- |
| Python package | PyPI as `shadownet-conformance` |
| Docker image | `ghcr.io/shadownet-protocol/conformance` |
| GitHub Action | Marketplace as `shadownet-protocol/conformance-action` |

## Specifications

- Protocol: [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](../shadownet-specs/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](../shadownet-specs/DEVELOPMENT.md)

## License

MIT.
