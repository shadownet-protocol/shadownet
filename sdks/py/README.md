# shadownet-py

[![PyPI](https://img.shields.io/pypi/v/shadownet)](https://pypi.org/project/shadownet/)
[![Python](https://img.shields.io/pypi/pyversions/shadownet)](https://pypi.org/project/shadownet/)
[![CI](https://github.com/shadownet-protocol/shadownet-py/actions/workflows/ci.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet-py/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](./LICENSE)
[![Typed](https://img.shields.io/badge/typing-PEP%20561-blue)](./src/shadownet/py.typed)

Python SDK for the [Shadownet protocol](https://github.com/shadownet-protocol/shadownet-specs). Implements the v0.1 RFCs in idiomatic, async, fully-typed Python.

## What this is

A reusable library — **not** a server, **not** a canonical core. It exposes the protocol primitives needed to build a Shadow:

- **DID resolution** — `did:key` (local) and `did:web` (async, cached, 16 KiB cap per RFC-0002).
- **Verifiable Credentials** — VC-JWT issuance + verification, freshness proofs, BitstringStatusList revocation (fail-closed >L1 per RFC-0003).
- **SCA client** — proof-session, issuance, freshness, callback-HMAC verification (RFC-0004).
- **SNS client** — async resolver with TTL + negative cache, signed-record verification (RFC-0005).
- **A2A profile** — session-token + Verifiable Presentation handshake; framework-agnostic verifier; optional FastAPI dependency (RFC-0006).
- **Webhooks** — outbound dispatcher with the spec retry schedule + degraded-state tracking; receiver-side verifier (RFC-0007).
- **MCP** — Pydantic models + a `Sidecar` Protocol + a one-call helper that wires every RFC-0007 tool onto a `FastMCP` server.

It is consumed by:

- [`hermes-social`](https://github.com/meghancampbel9/hermes-social) — the Sidecar reference implementation.
- `shadownet-cloud` — the multi-tenant Sidecar host (forthcoming).

Interop with `shadownet-go` and `shadownet-ts` is verified at the wire level by `shadownet-conformance`.

## Install

```bash
pip install shadownet
# or
uv add shadownet
```

Python 3.12+ required. For the optional FastAPI helpers:

```bash
pip install 'shadownet[fastapi]'
```

## Quick examples

### Issue and verify a credential

```python
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.vc.credential import issue_credential, new_credential, verify_credential

issuer_kp = Ed25519KeyPair.generate()
issuer_did = derive_did_key(issuer_kp.public_bytes)

subject_kp = Ed25519KeyPair.generate()
subject_did = derive_did_key(subject_kp.public_bytes)

cred = new_credential(
    issuer=issuer_did,
    subject=subject_did,
    level="urn:shadownet:level:L2",
    subject_type="person",
)
token = issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)

verified = await verify_credential(token, resolver=Resolver())
assert verified.level == "urn:shadownet:level:L2"
```

### Mint and verify a presentation

```python
from shadownet.vc.presentation import mint_presentation, verify_presentation
from shadownet.trust import TrustStore

verifier_did = "did:key:z6MkVerifier..."
vp_jwt = mint_presentation(
    holder_key=subject_kp,
    holder_did=subject_did,
    audience_did=verifier_did,
    credentials=[token],
)

trust = TrustStore.from_pairs([(issuer_did, ["urn:shadownet:level:L2"])])
result = await verify_presentation(
    vp_jwt,
    resolver=Resolver(),
    expected_audience=verifier_did,
    trust_store=trust,
)
assert len(result.credentials) == 1
```

### Run the inbound A2A handshake

```python
from shadownet.a2a.server import verify_handshake
from shadownet.sca.predicate import LevelLeaf

ctx = await verify_handshake(
    request_headers,                 # any Mapping[str, str]
    expected_audience=my_did,
    resolver=Resolver(),
    trust_store=trust,
    required_predicate=LevelLeaf(level="urn:shadownet:level:L2"),
)
# ctx.caller_did is the verified peer DID
# ctx.presentation.credentials are the credentials that survived every check
```

### Register the RFC-0007 MCP tools

```python
from mcp.server.fastmcp import FastMCP
from shadownet.mcp.register import register_shadownet_tools

server = FastMCP(name="my-sidecar")
register_shadownet_tools(server, my_sidecar_implementation)
# all required RFC-0007 tools are now exposed; opt into the optional ones with
# include_optional={"present", "audit"}
```

The full set of public APIs is curated under `shadownet.{crypto, did, vc, sca, sns, trust, a2a, webhook, mcp}`. See the `tests/integration/test_birthday_flow.py` for an end-to-end Sarah → Lukas walkthrough.

## Conformance

`tests/conformance/` validates every Pydantic wire model against the JSON Schemas in `shadownet-specs/schemas/`. CI fails on any drift.

## Develop

```bash
uv sync --all-extras           # runtime + dev + extras
uv run pytest                  # full suite (incl. conformance), with coverage
uv run pytest -m network       # opt-in network tests
uv run ruff check .            # lint
uv run ruff format .           # format
uv run mypy src/shadownet      # strict typing
```

Engineering conventions and contribution rules live in [`CLAUDE.md`](./CLAUDE.md).

## Specifications

- Protocol RFCs: [`shadownet-specs/rfcs`](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs)
- Wire-level walkthrough: [`shadownet-specs/examples/birthday-flow.md`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/examples/birthday-flow.md)
- Development plan: [`shadownet-specs/DEVELOPMENT.md`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/DEVELOPMENT.md)

## Versioning

Releases track the protocol version they implement (`0.1.x` while the spec is at v0.1). Pre-releases use the PEP 440 form (`0.1.0rc1`) and the matching git tag (`v0.1.0-rc.1`).

## License

[MIT](./LICENSE).
