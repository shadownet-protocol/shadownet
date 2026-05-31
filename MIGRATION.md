# Migration guide

This document explains what changed for consumers of the Shadownet SDKs,
the conformance suite, and the host-agent integrations after the
consolidation into the
[`shadownet-protocol/shadownet`](https://github.com/shadownet-protocol/shadownet)
monorepo.

If you don't depend on any of these, you can stop reading.

## Protocol v0.2 — what disappeared, what came back (`core/v0.3.x`)

The v0.2 RFC cut collapsed the v0.1 nine-RFC set into three RFCs and
changed the wire materially: DIDs are gone, W3C Verifiable Credentials
are gone, SNS-as-an-HTTP-service is gone (DNS-TXT replaces it), the SCA
proof state machine is gone, and a new addressing mode
(`shadow://key:z6Mk...@host:port` — direct, no DNS, no Provider) is now a
first-class peer to Shadowname addressing.

The `core/` subtree was rebuilt in lockstep with the spec cut. **The Go
SDK has been removed.** What `core/v0.3.0` ships:

- `cmd/provider-server` — multi-tenant Shadowname host (RFC 0001 §5.2).
- `cmd/issuer-server` — `org_affiliation` credential issuer + per-epoch
  revocation bitstring (RFC 0001 §6.4–§6.5). Supports both domain mode
  (well-known paths) and keyed-Hub mode (self-served AgentCard at
  `/.well-known/agent-card.json` declaring `shadownet:issueEndpoint` +
  `shadownet:statusListBase`).
- `core/pgstore/v0.3.x` — Postgres backend for both server stores.

What `core/v0.3.0` does **not** ship:

| Removed | Replacement |
| --- | --- |
| `pkg/sca`, `pkg/scaserver`, `cmd/sca-server` (SCA) | `cmd/issuer-server` (new role; different wire) |
| `pkg/sns`, `pkg/snsserver`, `cmd/sns-server` (SNS) | DNS-TXT resolution (no service) + `cmd/provider-server` |
| `pkg/did`, `pkg/vc`, `pkg/a2a` (DID + VC + A2A client) | python-sdk (`pip install shadownet`) — the canonical client SDK |
| `cmd/shadownet` (operator CLI subcommands beyond `keygen`/`inspect`) | python-sdk + the new `provider-server admin` / `issuer-server admin` subcommands |
| Any public `pkg/` API | All shared code is now under `internal/` |

GHCR image rename:

| Before (`core/v0.2.x` and earlier) | After (`core/v0.3.0`+) |
| --- | --- |
| `ghcr.io/shadownet-protocol/sca-server[:-pg]` | `ghcr.io/shadownet-protocol/issuer-server[:-pg]` |
| `ghcr.io/shadownet-protocol/sns-server[:-pg]` | `ghcr.io/shadownet-protocol/provider-server[:-pg]` |

The v0.1 images remain available at their old paths; only new releases
publish under the new names. Pin explicitly if you operate the v0.1
servers and aren't ready to migrate.

If you had a Go consumer of `github.com/shadownet-protocol/shadownet/core`
beyond the binaries: there is no public Go API after `core/v0.3.0`. Move
to the Python SDK (`pip install shadownet`) or pin to `core/v0.2.x`. The
in-tree primitives (JCS, AgentCard signing, credential JWT mint) live
under `internal/` and remain implementation detail.

The remainder of this document covers the v0.1 → consolidation migration
(repo move, import-path change, PyPI URL updates). Skip it if you're
already on the monorepo.

## What changed at the repo level

Three previously-separate code trees have been merged into one monorepo,
each living in its own subtree:

| Old location | New location |
| --- | --- |
| `shadownet-protocol/shadownet-go` | `shadownet-protocol/shadownet` → `core/` |
| `shadownet-protocol/shadownet-py` | `shadownet-protocol/shadownet` → `python-sdk/` |
| `shadownet-protocol/shadownet-conformance` | `shadownet-protocol/shadownet` → `conformance/` |

The host-agent integrations (Claude Code, Hermes Agent, OpenClaw plugin,
skill bundles) — previously a subdirectory of an upstream operator
deployment — also live here under `integrations/`.

## Go SDK

### Module path — BREAKING

| Before | After |
| --- | --- |
| `github.com/shadownet-protocol/shadownet-go` | `github.com/shadownet-protocol/shadownet/core` |
| `github.com/shadownet-protocol/shadownet-go/pgstore` | `github.com/shadownet-protocol/shadownet/core/pgstore` |

To migrate, in your project:

```sh
# Drop the old require/replace, add the new module:
go mod edit -droprequire github.com/shadownet-protocol/shadownet-go || true
go mod edit -dropreplace github.com/shadownet-protocol/shadownet-go || true
go get github.com/shadownet-protocol/shadownet/core@v0.2.0

# Mass-rewrite imports across the codebase:
find . -type f -name '*.go' -exec sed -i.bak \
  -e 's|github.com/shadownet-protocol/shadownet-go|github.com/shadownet-protocol/shadownet/core|g' {} +
find . -name '*.bak' -delete
go mod tidy
```

The `v0.1.x` releases of the old module path remain on the Go module proxy.
Existing consumers pinned to `v0.1.7` (or any earlier `v0.1.x`) continue to
work without changes; the migration is required only when you want to pick up
`v0.2.0` or later.

### CLI install

```sh
# Before:
go install github.com/shadownet-protocol/shadownet-go/cmd/shadownet@latest

# After:
go install github.com/shadownet-protocol/shadownet/core/cmd/shadownet@latest
```

### Container images

No change. The four reference images continue to publish at the same paths:

- `ghcr.io/shadownet-protocol/sca-server:<tag>`
- `ghcr.io/shadownet-protocol/sns-server:<tag>`
- `ghcr.io/shadownet-protocol/sca-server-pg:<tag>`
- `ghcr.io/shadownet-protocol/sns-server-pg:<tag>`

Tags reflect the new `core/vX.Y.Z` scheme starting at `v0.2.0`.

### Tag scheme

Within the monorepo, tags carry the directory prefix Go requires for
sub-module subtrees:

| Module | Tag pattern |
| --- | --- |
| Main module (`core/`) | `core/vX.Y.Z` |
| pgstore submodule | `core/pgstore/vX.Y.Z` |

Old `v0.1.x` and `pgstore/v0.1.x` tags remain on the legacy repository.

## Python SDK

### Imports & PyPI — no change required

The PyPI distribution (`shadownet`) and every importable name (`shadownet.*`)
are unchanged. The minimum supported Python version (`>= 3.12`) is unchanged.
Existing code continues to work without modification:

```python
# Still works in v0.2.0:
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.vc.credential import issue_credential, verify_credential
```

```sh
# Still works in v0.2.0:
pip install shadownet
uv add shadownet
```

### Package metadata

Repository URLs in package metadata now point at the monorepo:

| Field | Before | After |
| --- | --- | --- |
| `Homepage` | `…/shadownet-py` | `…/shadownet/tree/main/py` |
| `Issues` | `…/shadownet-py/issues` | `…/shadownet/issues` |
| `Repository` (new) | — | `…/shadownet` |
| `Changelog` (new) | — | `…/shadownet/blob/main/python-sdk/CHANGELOG.md` |

Update any internal dashboards or CI scripts that cross-reference these URLs.

### Tag scheme

| Releases | Before | After |
| --- | --- | --- |
| Git tag | `v0.1.3` | `python-sdk/v0.2.0` |
| PyPI version | `0.1.3` | `0.2.0` |

The `0.1.x` releases remain on PyPI and are unaffected.

### Why `0.2.0` if there's no API change?

The version bump is for parity with the Go SDK's breaking import-path change
and to mark the migration cleanly. There is no Python API change between
`0.1.3` and `0.2.0`.

## Conformance suite

### Distribution name & CLI — no change required

The PyPI distribution (`shadownet-conformance`), the CLI entry points
(`shadownet-conformance` and `shadownet-conformance-fixtures`), and the
container image (`ghcr.io/shadownet-protocol/conformance`) are unchanged.
The published GitHub Action
(`shadownet-protocol/conformance-action@v0.1`) keeps working — external
implementations consuming it for their CI need no changes.

### Runtime SDK pin

The conformance suite's `shadownet` runtime dep moves from `>=0.1.3,<0.2`
to `>=0.2.0,<0.3` to track the renamed Python SDK.

### Tag scheme

| Releases | Before | After |
| --- | --- | --- |
| Git tag | `v0.1.1` | `conformance/v0.2.0` |
| PyPI version | `0.1.1` | `0.2.0` |

Old `v0.1.x` releases of `shadownet-conformance` remain on PyPI / GHCR /
GitHub Releases on the legacy repo.

## Integrations (host-agent plugins)

Existing installs of the OpenClaw plugin from npm, Claude Code plugins
from a `marketplace.json` link, or `.well-known/skills/index.json` URLs
continue to work unchanged. The npm distribution name
(`@shadownet-protocol/openclaw-plugin`), the Claude Code plugin manifest schema,
and the skill bundle shape (agentskills.io) are all unchanged.

The integrations are protocol-level — they consume the spec's public
surfaces (RFC-0006 MCP at `/u/<shadowname>/mcp`, RFC-0007 HMAC-signed
webhooks) and work with any RFC-compliant Sidecar.

## Where to file issues now

Use [`shadownet-protocol/shadownet/issues`](https://github.com/shadownet-protocol/shadownet/issues).
The legacy repos' issue trackers are no longer monitored.
