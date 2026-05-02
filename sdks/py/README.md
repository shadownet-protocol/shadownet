# shadownet-py

Python SDK for the [Shadownet](../shadownet-specs/) protocol.

## Status

Early. No code yet. Implements the v0.1 RFCs at [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/).

## What this repo is

A reusable Python library — **not** a server, **not** a canonical core. The primitives (DID resolution, VC issuance/verification, VP minting, A2A client + server helpers, MCP tool definitions) used by:

- [`hermes-social`](https://github.com/meghancampbel9/hermes-social) — the Sidecar reference implementation.
- `shadownet-cloud` — the cloud signup + multi-tenant Sidecar host (forthcoming).
- Anything else that wants to speak Shadownet from Python.

Interop with other SDKs (`shadownet-go`, `shadownet-ts`) is verified at the wire level by [`shadownet-conformance`](../shadownet-specs/DEVELOPMENT.md).

## Tooling

- **Package manager**: [`uv`](https://docs.astral.sh/uv/)
- **Python**: 3.12+
- **Style/lint**: `ruff`
- **Tests**: `pytest` (+ `pytest-asyncio`)
- **License**: MIT

## Install / Develop

```bash
uv sync --all-extras           # install runtime + dev + extras
uv run pytest                  # run the test suite
uv run pytest -m network       # opt-in: tests that hit the network
uv run ruff check .            # lint
uv run ruff format .           # format
```

Engineering conventions and contribution rules live in [`CLAUDE.md`](./CLAUDE.md).

## Planned layout

```
src/shadownet/
  crypto.py         Ed25519, JWT/JWS
  did.py            did:key, did:web
  vc.py             VC-JWT issuance + verification + freshness + BitstringStatusList
  a2a/
    client.py       outbound A2A (handshake, message:send, message:stream)
    server.py       inbound A2A helpers (FastAPI-friendly)
  sca.py            SCA helpers (CSR building, predicate evaluation)
  sns.py            SNS helpers (record signing, resolution, caching)
  mcp.py            MCP tool definitions matching RFC-0007
tests/
pyproject.toml
uv.lock
```

The directory tree is not committed yet — added incrementally as work lands.

## Specifications

- Protocol: [`shadownet-specs/rfcs`](../shadownet-specs/rfcs/)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](../shadownet-specs/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](../shadownet-specs/DEVELOPMENT.md)

## License

TBD.
