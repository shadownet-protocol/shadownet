# Shadownet — `core/` (reference Provider + Issuer servers)

> **Status: v0.2 migration in flight.** This directory is in the middle of
> being rebuilt from a Go SDK + reference SCA/SNS servers (protocol v0.1)
> into two Go reference HTTP server binaries (protocol v0.2): the
> **Provider** and the **Issuer**. Phase 1 of the migration removes the
> v0.1 surface; Phases 2–7 land the v0.2 binaries. See the plan at
> `/Users/perfect/.claude-work/plans/resilient-hugging-graham.md` for the
> phased roadmap and the [`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs)
> repository (RFC 0001 v0.2) for the wire spec.

## What this is becoming

Two Go reference server binaries for self-hosters and Hubs:

- **`cmd/provider-server`** — hosts signed A2A AgentCards at
  `<ep>/identity/<local>` (RFC 0001 §5.2). Used by Shadowname providers
  (sh4dow.org-style operators, orgs hosting employee Shadownames, Hubs
  hosting member Shadownames).
- **`cmd/issuer-server`** — issues `org_affiliation` credentials (CSR in /
  credential out, RFC 0001 §6.5) and serves the per-epoch revocation
  bitstring (§6.4). Supports both **domain-identified issuers**
  (well-known paths) and **keyed-Hub mode** (self-served AgentCard with
  `shadownet:issueEndpoint` and `shadownet:statusListBase` declared
  inline).

There is **no public Go SDK** in v0.2. The canonical Shadownet SDK is
[`../python-sdk/`](../python-sdk/) (PyPI: `shadownet`). All v0.2 wire-level
helpers (JCS, AgentCard signing, credential mint, CSR validation, status
bitstring) live under `core/internal/` as implementation details of the
two binaries — not a stable Go API.

## Storage

SQLite by default (zero-config, single-binary self-host). The
[`pgstore`](./pgstore/) submodule adds a Postgres backend for production
deployments; operators who need PG depend on it explicitly so the default
binaries stay free of the `pgx` dependency graph.

## v0.1 users

The v0.1 protocol Go releases stay reachable on the Go proxy at
`core/v0.2.x`. The first v0.2-protocol Go release will be tagged
`core/v0.3.0` once Phases 1–7 land.

## License

MIT.
