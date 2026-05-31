# Shadownet

> A protocol that lets personal AI agents discover, verify, and coordinate
> with each other on behalf of their humans — without leaking private context.

[![Core CI](https://github.com/shadownet-protocol/shadownet/actions/workflows/core.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/core.yml)
[![Python SDK CI](https://github.com/shadownet-protocol/shadownet/actions/workflows/python-sdk.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/python-sdk.yml)
[![Conformance](https://github.com/shadownet-protocol/shadownet/actions/workflows/conformance.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

This repository hosts the official **Python SDK** and the **reference Go
server binaries** (`provider-server`, `issuer-server`) for the
[Shadownet protocol](https://github.com/shadownet-protocol/shadownet-specs).

If Shadownet is useful — or it could be — please ⭐ this repo. It's the
cheapest signal you can send a small open-source project that the work
matters.

## What is Shadownet?

Most of us already have a personal agent (Hermes, OpenClaw, Claude, …). They
are capable and **completely alone** — they can't talk to each other.
Shadownet is the protocol layer that fixes that, built on open standards
([Google A2A](https://a2a-protocol.org/),
[Anthropic MCP](https://modelcontextprotocol.io)):

- **Shadowname addressing.** `alice@sh4dow.org` resolves via DNS-TXT to a
  Provider that serves a signed A2A AgentCard.
- **Direct addressing.** `shadow://key:z6Mk...@host:port` bypasses DNS for
  self-hosted Hubs and key-identified peers.
- **Org-affiliation credentials.** A `shadownet-cred+jwt` (signed by an
  Issuer) lets one Shadow prove "I belong to acme.example" to another
  Shadow as part of the A2A handshake.

The full picture is in the spec repo: the
[philosophy](https://github.com/shadownet-protocol/shadownet-specs#philosophy),
the [v0.2 RFC set](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs),
and the appendix walkthroughs covering both the typed (birthday-flow) and
free-form coordination handshakes.

## What's in this repo

```
shadownet/
├── core/            Go reference servers (cmd/provider-server + cmd/issuer-server); Postgres backend in pgstore/. No public Go SDK.
├── python-sdk/      Python SDK (PyPI: shadownet) — the canonical client SDK; consumed by shadownet-local and downstream Sidecar deployments
├── conformance/     Wire-level interop test suite (PyPI: shadownet-conformance) + ghcr.io/shadownet-protocol/conformance image + GitHub Action
├── integrations/    Host-agent plugins (Claude Code, Hermes Agent, OpenClaw, raw skill bundles)
├── examples/        Runnable end-to-end examples (one per language)
├── CONTRIBUTING.md, SECURITY.md, MIGRATION.md, …
└── .github/         Workflows, issue templates, Dependabot config
```

What lives where, and why:

- **`core/`** ships **two reference HTTP servers** for the two operator
  roles in RFC 0001: `provider-server` (multi-tenant Shadowname host;
  serves signed AgentCards at `<ep>/identity/<local>`) and `issuer-server`
  (issues `org_affiliation` credentials, serves the per-epoch revocation
  bitstring; supports both DNS-routed and key-identified Hubs). It is
  **not** a public Go SDK — all shared code lives under `internal/`.
- **`python-sdk/`** is the **canonical client SDK** — downstream Sidecar
  deployments compose it with
  [`shadownet-local`](https://github.com/shadownet-protocol/shadownet-local) or
  any other A2A-capable host runtime.
- **`conformance/`** is the **cross-implementation wire-level test suite**.
  Run it against any RFC-compliant SCA / SNS / Sidecar and it tells you
  whether they're correct — language-agnostic, talks the wire only. Ships
  as a PyPI distribution, a GHCR container image, and a GitHub Action
  (`shadownet-protocol/conformance-action@v0.1`).
- **`integrations/`** are **protocol-level host-agent plugins** — they wire
  agents (Claude Code, Hermes Agent, OpenClaw, …) to *any* Shadownet
  Sidecar via the spec's public surfaces (RFC-0006 MCP at
  `/u/<shadowname>/mcp`, RFC-0007 HMAC-signed webhooks). They are not
  bound to any one operator; an optional `integration-bundle` endpoint
  can fetch tenant configuration in one call when the host Sidecar
  publishes one.

The protocol RFCs and JSON Schemas live in
[`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs);
this monorepo's `conformance/` workflow pulls them at a pinned ref and runs
against in-tree reference servers on every PR.

## Get started

### Python developers

```sh
pip install shadownet
# or: uv add shadownet
```

Quickstart and API surface: [`python-sdk/README.md`](./python-sdk/README.md) ·
[PyPI](https://pypi.org/project/shadownet/)

### Operators (run a Provider or Issuer)

`core/` ships container images for `provider-server` and `issuer-server`
plus a sample `docker-compose.yml`. See the
[operator quickstart](./core/README.md) and [`core/deploy/`](./core/deploy/).

## Architecture at a glance

```
                       issues shadownet-cred+jwt
   +----------+ ---------------------------------> +-----------+
   |  Issuer  |                                    |  Subject  |
   +----------+                                    |  (Shadow) |
                                                   +-----------+
                                                         |
                                          registers Shadowname (local + pk)
                                                         v
                                                  +-----------+
                                                  |  Provider |
                                                  +-----------+
                                                         ^
                                              alice@sh4dow.org → DNS-TXT
                                                         |
   +-----------+              +-----------+              +     +-----------+
   |  Shadow A | <==A2A handshake (signed AgentCard)========>  |  Shadow B |
   +-----------+              +-----------+                    +-----------+
         ^                                                          ^
         | MCP                                                  MCP |
   +-----------+                                               +-----------+
   |  Human A  |                                               |  Human B  |
   +-----------+                                               +-----------+
```

Agents speak A2A to each other; they speak MCP to their human. A human only
ever sees content from their own agent — never from another person's agent
claiming to speak for them. Direct addressing (`shadow://key:z6Mk...@host:port`)
bypasses Provider + DNS entirely for self-hosted Hubs.

## Implementations & related repos

| Repo | Status | Role |
| --- | --- | --- |
| [`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs) | Active | RFCs, JSON Schemas, fixture seeds — protocol source of truth |
| **`shadownet`** (this repo) | Active | Python SDK, reference Provider + Issuer servers, conformance suite, host-agent integrations |
| [`shadownet-local`](https://github.com/shadownet-protocol/shadownet-local) | Active | Sidecar reference implementation; drop-in for any A2A-capable runtime |
| `shadownet-ts` | Planned | TypeScript SDK for browser + Node |

## Versioning & releases

Each SDK is released independently; the monorepo tag scheme prefixes each
subtree's tags with its directory:

| Tag pattern | Subject |
| --- | --- |
| `core/vX.Y.Z` | Reference Provider + Issuer binaries (GHCR images) |
| `core/pgstore/vX.Y.Z` | Postgres backend submodule |
| `python-sdk/vX.Y.Z` | Python SDK (PyPI: `shadownet`) |

Each subtree maintains its own `CHANGELOG.md`. The protocol version
(currently `v0.2`) is independent of subtree versions.

## Migrating from `shadownet-go` / `shadownet-py`

The Python SDK was previously published from a standalone repo; the legacy
Go SDK shipped from `shadownet-go`. See [`MIGRATION.md`](./MIGRATION.md) for
the v0.2 protocol cut and what changed for existing consumers (Go SDK
deprecation, Python URL updates, tag scheme).

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). PRs welcome on either SDK,
cross-cutting examples, docs, or CI.

## Security

Found a vulnerability? Please don't open a public issue. See
[`SECURITY.md`](./SECURITY.md) for the disclosure process.

## License

[MIT](./LICENSE).
