# python-sdk/CLAUDE.md

Conventions for the Python SDK (`shadownet` on PyPI). Cross-cutting rules in the root [`CLAUDE.md`](../CLAUDE.md); protocol authority in [`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs).

## Protocol version

This SDK targets **Shadownet v0.2** (extension URI `urn:shadownet:0.2`). RFCs are at:

- [`rfcs/0001-shadownet.md`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0001-shadownet.md) — wire spec
- [`rfcs/0002-shadownet-mcp.md`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0002-shadownet-mcp.md) — MCP control surface
- [`rfcs/0003-shadownet-onboarding.md`](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0003-shadownet-onboarding.md) — onboarding URI

JSON Schemas for wire artifacts live in [`shadownet-specs/schemas/`](https://github.com/shadownet-protocol/shadownet-specs/tree/main/schemas).

## When implementing an RFC or external protocol

**Always fetch the official, trusted source of the spec and read it before writing implementation code.** Do not work from memory or summary — IETF / W3C / ECMA text is the ground truth, and our signatures depend on bit-exact compliance.

**Check frequently, not just once.** Specs and reference implementations move. Before every non-trivial change to a module that depends on an external spec or library, re-check upstream:

- For active protocols (A2A, MCP, Shadownet itself), re-read the relevant section in upstream HEAD.
- For RFCs at IETF, RFCs are stable — but errata and updates can land.
- For libraries (dnspython, httpx, pydantic, mcp, a2a-sdk, etc.), check the installed version against the latest release, scan the changelog for the relevant API surface, and re-verify the call signature you're about to use.
- No amount of reading is "enough" — it never hurts to re-check.

Trusted sources for the specs this SDK touches:

| Spec | Trusted URL |
| --- | --- |
| Shadownet v0.2 (this protocol) | <https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs> |
| A2A v1.0 (Agent-to-Agent) | <https://a2a-protocol.org/> |
| MCP (Model Context Protocol) | <https://modelcontextprotocol.io/> |
| RFC 8785 (JSON Canonicalization, JCS) | <https://www.rfc-editor.org/rfc/rfc8785> |
| RFC 8032 (Ed25519 / EdDSA) | <https://www.rfc-editor.org/rfc/rfc8032> |
| RFC 7515 (JWS) / RFC 7519 (JWT) | <https://www.rfc-editor.org/rfc/rfc7515> · <https://www.rfc-editor.org/rfc/rfc7519> |
| RFC 8417 (`+jwt` JWS `typ` convention) | <https://www.rfc-editor.org/rfc/rfc8417> |
| RFC 7807 (`application/problem+json`) | <https://www.rfc-editor.org/rfc/rfc7807> |
| RFC 1035 (DNS TXT, string chaining) | <https://www.rfc-editor.org/rfc/rfc1035> |
| RFC 8446 (TLS 1.3) | <https://www.rfc-editor.org/rfc/rfc8446> |
| RFC 9728 (Protected Resource Metadata, future) | <https://www.rfc-editor.org/rfc/rfc9728> |
| Multibase / Multicodec | <https://github.com/multiformats/multibase> · <https://github.com/multiformats/multicodec> |

Add new entries here in the same PR that introduces the dep on the spec.

### Local clones of upstream reference repos

The user keeps fresh clones of the active reference repos for fast browsing. **Always prefer reading from these local clones over WebFetch when looking for protocol detail or reference-implementation behavior** — they're guaranteed to match HEAD, they're greppable, and you avoid roundtrip cost. Pull periodically; check `git log -1` to see how stale the local copy is before relying on it.

| Repo | Local path | Notable files |
| --- | --- | --- |
| A2A spec (`a2aproject/A2A`) | `../../dev/A2A` | `docs/specification.md` (full normative spec) · `docs/topics/` (extensions, multi-tenancy, agent-discovery, life-of-a-task) · `specification/a2a.proto` (protobuf source-of-truth) · `docs/topics/extensions.md` (how Shadownet extension fields ride A2A) |
| A2A Python SDK (`a2aproject/a2a-python`, PyPI `a2a-sdk`) | `../../dev/a2a-python` | `src/a2a/types/` (typed AgentCard/Message/Task models) · `src/a2a/utils/signing.py` (reference AgentCard signing/verifying impl — JCS, JWS detached-style assembly) · `src/a2a/client/card_resolver.py` (reference AgentCard fetch logic) · `src/a2a/server/routes/agent_card_routes.py` (server-side card hosting) |
| Shadownet specs | `../../shadownet-specs` (sibling to monorepo) | `rfcs/0001-shadownet.md` · `rfcs/0002-shadownet-mcp.md` · `rfcs/0003-shadownet-onboarding.md` · `schemas/` |

When `git log -1` on a local clone shows it's more than a few weeks behind HEAD, surface that to the user — they may want to `git pull` before you proceed.

### Library version checks

Before writing code that depends on a Python library's specific behavior:

1. Find the installed version: `grep '^name = "<lib>"' uv.lock | head` or `uv pip show <lib>`.
2. Check what's current: WebFetch the PyPI project page or the changelog (e.g. `<https://pypi.org/project/<lib>/>`).
3. If we're more than one minor behind and the changelog mentions the API surface you're using, mention it to the user — they may want to upgrade before you build on a soon-to-change surface.

## Dependency docs — read these before writing calling code

When touching a module that uses one of these libraries, verify the API against the official docs before writing the call (the alternative is recalling stale training data, which has burned us before — see [memory: `verify-library-docs`](../../.claude-work/projects/-Users-perfect-shadownet-shadownet/memory/feedback_verify_library_docs.md)).

| Library | Docs |
| --- | --- |
| dnspython | <https://dnspython.readthedocs.io/en/stable/> — for `_shadownet.<domain>` TXT lookup (RFC 0001 §4.2). Key API: `dns.resolver.Resolver().resolve(name, rdtype="TXT")` → `Answer`; iterate `rrset` for `dns.rdtypes.ANY.TXT.TXT` rdata with `.strings: Tuple[bytes, ...]` (RFC 1035 chained segments). Exceptions: `dns.resolver.NXDOMAIN`, `dns.resolver.NoAnswer`, `dns.resolver.LifetimeTimeout`. |
| httpx | <https://www.python-httpx.org/> — for AgentCard fetch, status list fetch, CSR POST. Both sync (`httpx.Client`) and async (`httpx.AsyncClient`) APIs used. |
| pydantic | <https://docs.pydantic.dev/latest/> — wire models use `ConfigDict(extra="forbid", populate_by_name=True)`. Aliases use camelCase to match RFC 0001 §2 naming. |
| cryptography (Ed25519) | <https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/> — raw Ed25519 sign/verify. Wrapped by `shadownet.crypto.ed25519.Ed25519KeyPair`. |
| PyJWT | <https://pyjwt.readthedocs.io/en/stable/> — JWS-compact EdDSA encode/decode. Wrapped by `shadownet.crypto.jwt`. v0.2 uses `typ` values `shadownet-env+jwt`, `shadownet-cred+jwt`, `shadownet-csr+jwt`. |
| mcp | <https://modelcontextprotocol.io/> + <https://github.com/modelcontextprotocol/python-sdk> — for the MCP control surface (RFC 0002). |
| a2a-sdk | <https://a2a-protocol.org/> + <https://github.com/google-a2a/a2a-python> — A2A v1.0 client/server. Shadownet rides A2A as an extension. |
| dnspython DNSSEC | <https://dnspython.readthedocs.io/en/stable/dnssec.html> — RFC 0001 §4.3 marks DNSSEC RECOMMENDED. Resolver-side opt-in. |

If a library not listed here gets added, document it in this table in the same PR.

## Code conventions

- **Naming.** Per RFC 0001 §2: JSON keys camelCase (use pydantic `alias`), value strings snake_case, JWS `typ` kebab + `+jwt`. Python identifiers are snake_case; expose camelCase only on the wire via aliases.
- **Errors.** Wire-mapped errors raise `shadownet.errors.<Code>Error` subclasses of `ShadownetError`. Receiver-side error responses serialize to RFC 7807 `application/problem+json` per RFC 0001 §8.8.
- **No backwards-compatibility shims** while the protocol is at v0.1/v0.2 (root CLAUDE.md). Old types/functions get deleted, not deprecated.
- **No emojis, no banner comments, no multi-paragraph docstrings.** One-sentence module/class/function docstrings; non-obvious WHY-comments only.
- **Strict mypy.** `pyproject.toml` is `strict = true`; type every public surface.

## Local gate

Before pushing any change to `python-sdk/src/`:

```sh
cd python-sdk
uv run ruff check .
uv run ruff format --check .
uv run mypy src/shadownet
uv run pytest
```

All four must pass. No `# type: ignore` without a comment naming the upstream issue.

## Module layout (v0.2 target)

| Module | RFC | Purpose |
| --- | --- | --- |
| `crypto/` | §4.1 | Ed25519 keys, JWS-compact EdDSA, multibase z-base58. |
| `jcs.py` | §2 | JCS canonical-JSON serializer (RFC 8785) for `msgHash` and signature inputs. |
| `identifiers.py` | §3 | Shadowname / domain / multibase-pk parsing and validation. |
| `credential/` | §6 | Mint, verify, status-list fetch+check. One kind: `org_affiliation`. |
| `csr.py` | §6.5 | Mint and validate `shadownet-csr+jwt`. |
| `trust.py` | §7 | Flat issuer/accept-kinds list, acceptance policy `{fromContact, fromStranger}`, evaluation. |
| `dns.py` | §4.2 | Lookup `_shadownet.<domain>` TXT. |
| `agentcard.py` | §5 | Fetch + verify A2A AgentCard at `<ep>/identity/<local>`. |
| `envelope.py` | §8 | Mint, validate, `msgHash` computation. |
| `a2a.py` | §8 | A2A `message:send` wrapping; problem+json error mapping. |
| `receiver/` | §8.6, §9 | Validation pipeline + classification (inbox/stranger_review/rejected) + auto-add rule. |
| `provider/` | §5.2 | HTTP server for `/identity/<local>`. |
| `issuer/` | §6.4–§6.5 | HTTP server for `/.well-known/shadownet/issue` and `/.well-known/shadownet/status/<epoch>`. |
| `mcp/` | RFC 0002 | Sidecar MCP server with the v0.2 tool set. |
| `onboarding/` | RFC 0003 | `shadow://connect` parser, handoff redemption, refresh client. |