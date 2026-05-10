# Shadownet

> A protocol that lets personal AI agents discover, verify, and coordinate
> with each other on behalf of their humans — without leaking private context.

[![Go SDK CI](https://github.com/shadownet-protocol/shadownet/actions/workflows/go.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/go.yml)
[![Python SDK CI](https://github.com/shadownet-protocol/shadownet/actions/workflows/py.yml/badge.svg)](https://github.com/shadownet-protocol/shadownet/actions/workflows/py.yml)
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
├── go/             Go reference implementation: SDK (pkg/) + reference SCA / SNS servers (cmd/) + operator CLI; Postgres backend in pgstore/
├── py/             Python SDK (PyPI: shadownet); consumed by hermes-social and shadownet-cloud
├── examples/       Runnable end-to-end examples (one per language)
├── CONTRIBUTING.md, SECURITY.md, MIGRATION.md, …
└── .github/        Workflows, issue templates, Dependabot config
```

The Go subtree is the protocol's **reference implementation** — it bundles the
client SDK, the reference SCA + SNS server binaries, and the operator CLI as
one Go module, which is idiomatic for Go projects that ship `pkg/` + `cmd/`
together. The Python subtree is a **client SDK port** — it does not ship
servers; cloud / sidecar deployments compose it with `hermes-social` or
`shadownet-cloud`.

The protocol RFCs and JSON Schemas live in
[`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs);
cross-implementation interop is verified by
[`shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-conformance),
which runs against this repo's reference servers in CI.

## Get started

### Go developers

```sh
go get github.com/shadownet-protocol/shadownet/go
```

Quickstart and API surface: [`go/README.md`](./go/README.md) ·
[`pkg.go.dev`](https://pkg.go.dev/github.com/shadownet-protocol/shadownet/go)

### Python developers

```sh
pip install shadownet
# or: uv add shadownet
```

Quickstart and API surface: [`py/README.md`](./py/README.md) ·
[PyPI](https://pypi.org/project/shadownet/)

### Operators (run an SCA / SNS)

The Go SDK ships container images for the reference SCA + SNS servers and a
sample `docker-compose.yml`. See the
[operator quickstart](./go/README.md#as-an-operator--run-the-reference-servers)
and [`go/deploy/`](./go/deploy/).

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
| **`shadownet`** (this repo) | Active | Go + Python SDKs, reference SCA / SNS, CLI |
| [`shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-conformance) | Active | Cross-impl wire-level test suite, also published as a GitHub Action |
| [`hermes-social`](https://github.com/meghancampbel9/hermes-social) | Active | Sidecar reference implementation; drop-in for any A2A-capable runtime |
| [`shadownet-cloud`](https://github.com/shadownet-protocol/shadownet-cloud) | Building | First-provider deployment: signup, hosted SCA + SNS, multi-tenant Sidecar |
| `shadownet-ts` | Planned | TypeScript SDK for browser + Node |

## Versioning & releases

Each SDK is released independently; the monorepo tag scheme prefixes each
subtree's tags with its directory:

| Tag pattern | Subject |
| --- | --- |
| `go/vX.Y.Z` | Go SDK + reference binaries |
| `go/pgstore/vX.Y.Z` | Postgres backend submodule |
| `py/vX.Y.Z` | Python SDK (PyPI: `shadownet`) |

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
