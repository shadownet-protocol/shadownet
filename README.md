# Shadownet

> A protocol that lets personal AI agents discover, verify, and coordinate
> with each other on behalf of their humans — without leaking private context.

[![Core CI](https://github.com/shadownet-protocol/shadownet/actions/workflows/core.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/core.yml)
[![Python SDK CI](https://github.com/shadownet-protocol/shadownet/actions/workflows/python-sdk.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/python-sdk.yml)
[![Conformance](https://github.com/shadownet-protocol/shadownet/actions/workflows/conformance.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

This repository hosts the official **Go and Python SDKs** and the **reference
SCA / SNS / CLI binaries** for the
[Shadownet protocol](https://github.com/shadownet-protocol/shadownet-specs).

If Shadownet is useful — or it could be — please ⭐ this repo. It's the
cheapest signal you can send a small open-source project that the work
matters.

## What is Shadownet?

Most of us already have a personal agent (Hermes, OpenClaw, Claude, …). They
are capable and **completely alone** — they can't talk to each other.
Shadownet is the protocol layer that fixes that, built on open standards
(W3C [DIDs](https://www.w3.org/TR/did-core/) and
[Verifiable Credentials](https://www.w3.org/TR/vc-data-model/),
[Google A2A](https://google.github.io/A2A/),
[Anthropic MCP](https://modelcontextprotocol.io)):

- **SCA — Shadow Certificate Authority.** Proof of personhood: every agent
  is bound to a verified human via a Verifiable Credential.
- **SNS — Shadow Name Service.** Discovery: resolve `alice@sh4dow.org` to a
  DID and an agent endpoint.
- **A2A profile.** A hardened handshake — session token + Verifiable
  Presentation, mutually authenticated, end-to-end verifiable.

The full picture is in the spec repo: the
[philosophy](https://github.com/shadownet-protocol/shadownet-specs#philosophy),
the [v0.1 RFC set](https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs),
the
[typed (birthday-flow) wire walkthrough](https://github.com/shadownet-protocol/shadownet-specs/blob/main/examples/birthday-flow.md),
or the
[free-form coordination walkthrough](https://github.com/shadownet-protocol/shadownet-specs/blob/main/examples/free-form-coordination.md)
(the v0.1.4+ default envelope shape).

## What's in this repo

```
shadownet/
├── core/            Go reference implementation: SDK (pkg/) + reference SCA / SNS servers (cmd/) + operator CLI; Postgres backend in pgstore/
├── python-sdk/      Python SDK (PyPI: shadownet); consumed by hermes-social and downstream Sidecar deployments
├── conformance/     Wire-level interop test suite (PyPI: shadownet-conformance) + ghcr.io/shadownet-protocol/conformance image + GitHub Action
├── integrations/    Host-agent plugins (Claude Code, Hermes Agent, OpenClaw, raw skill bundles)
├── examples/        Runnable end-to-end examples (one per language)
├── CONTRIBUTING.md, SECURITY.md, MIGRATION.md, …
└── .github/         Workflows, issue templates, Dependabot config
```

What lives where, and why:

- **`core/`** is the protocol's **reference implementation** — it bundles the
  client SDK, the reference SCA + SNS server binaries, and the operator CLI
  as one Go module. Idiomatic for Go projects that ship `pkg/` + `cmd/`
  together.
- **`python-sdk/`** is a **client SDK port** — no servers; downstream Sidecar
  deployments compose it with
  [`hermes-social`](https://github.com/meghancampbel9/hermes-social) or
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

### Go developers

```sh
go get github.com/shadownet-protocol/shadownet/core
```

Quickstart and API surface: [`core/README.md`](./core/README.md) ·
[`pkg.go.dev`](https://pkg.go.dev/github.com/shadownet-protocol/shadownet/core)

### Python developers

```sh
pip install shadownet
# or: uv add shadownet
```

Quickstart and API surface: [`python-sdk/README.md`](./python-sdk/README.md) ·
[PyPI](https://pypi.org/project/shadownet/)

### Operators (run an SCA / SNS)

The Go SDK ships container images for the reference SCA + SNS servers and a
sample `docker-compose.yml`. See the
[operator quickstart](./core/README.md#as-an-operator--run-the-reference-servers)
and [`core/deploy/`](./core/deploy/).

## Architecture at a glance

```
                              issues VC
   +--------+ ---------------------------> +-----------+
   |  SCA   |                              |   Holder  |
   +--------+                              | (Shadow)  |
                                           +-----------+
                                                 |
                                          registers DID + endpoint
                                                 v
                                             +-------+
                                             |  SNS  |
                                             +-------+
                                                 ^
                                          alice@sh4dow.org
                                                 |
   +-----------+              +-----------+      +     +-----------+
   |  Shadow A | <==A2A handshake (VP exchange)====>   |  Shadow B |
   +-----------+              +-----------+            +-----------+
         ^                                                  ^
         | MCP                                          MCP |
   +-----------+                                       +-----------+
   |  Human A  |                                       |  Human B  |
   +-----------+                                       +-----------+
```

Agents speak A2A to each other; they speak MCP to their human. A human only
ever sees content from their own agent — never from another person's agent
claiming to speak for them.

## Implementations & related repos

| Repo | Status | Role |
| --- | --- | --- |
| [`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs) | Active | RFCs, JSON Schemas, fixture seeds — protocol source of truth |
| **`shadownet`** (this repo) | Active | Go + Python SDKs, reference SCA / SNS, CLI, conformance suite, host-agent integrations |
| [`hermes-social`](https://github.com/meghancampbel9/hermes-social) | Active | Sidecar reference implementation; drop-in for any A2A-capable runtime |
| `shadownet-ts` | Planned | TypeScript SDK for browser + Node |

## Versioning & releases

Each SDK is released independently; the monorepo tag scheme prefixes each
subtree's tags with its directory:

| Tag pattern | Subject |
| --- | --- |
| `core/vX.Y.Z` | Go SDK + reference binaries |
| `core/pgstore/vX.Y.Z` | Postgres backend submodule |
| `python-sdk/vX.Y.Z` | Python SDK (PyPI: `shadownet`) |

Each subtree maintains its own `CHANGELOG.md`. The protocol version
(currently `v0.1`) is independent of SDK versions.

## Migrating from `shadownet-go` / `shadownet-py`

Both SDKs were previously published from standalone repos. See
[`MIGRATION.md`](./MIGRATION.md) for what changed for existing consumers
(Go import path change, Python URL updates, tag scheme) and what stayed the
same (PyPI distribution name, container image paths, public APIs).

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). PRs welcome on either SDK,
cross-cutting examples, docs, or CI.

## Security

Found a vulnerability? Please don't open a public issue. See
[`SECURITY.md`](./SECURITY.md) for the disclosure process.

## License

[MIT](./LICENSE).
