# Shadownet — `core/` (reference Provider + Issuer servers)

Go reference HTTP server binaries for Shadownet **protocol v0.2**.

- **`cmd/provider-server`** — multi-tenant Shadowname host. Serves signed
  A2A AgentCards at `<ep>/identity/<local>` per [RFC 0001
  §5.2](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0001-shadownet.md).
  Use it if you operate a Shadowname provider domain (a SaaS like
  `sh4dow.org`, an org hosting employee Shadownames, or a Hub hosting
  member Shadownames).

- **`cmd/issuer-server`** — `org_affiliation` credential issuer plus
  per-epoch revocation bitstring service ([RFC 0001 §6.4–§6.5
  ](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0001-shadownet.md)).
  Supports both **domain mode** (well-known paths under
  `/.well-known/shadownet/`) and **keyed-Hub mode** (self-served
  AgentCard at `/.well-known/agent-card.json` with `shadownet:
  issueEndpoint` + `shadownet:statusListBase` declarations).

There is **no public Go SDK** in v0.2. The canonical Shadownet SDK is
[`../python-sdk/`](../python-sdk/) (PyPI: `shadownet`). All v0.2 wire-level
helpers (JCS, AgentCard signing, credential mint, CSR validation, status
bitstring) live under `core/internal/` as implementation details of the
two binaries — not a stable Go API.

## Quickstart (SQLite, loopback)

```sh
# 1. Generate keys (these live on the operator's disk only)
go run ./internal/cli keygen --out ./provider.jwk
go run ./internal/cli keygen --out ./issuer.jwk

# 2. Write provider.yaml + issuer.yaml (see deploy/*.yaml for annotated
#    examples).

# 3. Compute the DNS TXT record the provider domain needs (skip if you
#    plan to operate in direct/keyed mode only)
go run ./cmd/provider-server dns-record --config provider.yaml

# 4. Run the servers
go run ./cmd/provider-server serve --config provider.yaml &
go run ./cmd/issuer-server serve --config issuer.yaml &

# 5. Register your first Shadow with the Provider
go run ./cmd/provider-server admin add \
    --config provider.yaml \
    --local alice \
    --pk z6Mk... \
    --a2a-url https://shadow.example.com/v1/a2a/alice

# 6. Watch the Issuer's pending queue and approve/reject
go run ./cmd/issuer-server admin list-pending --config issuer.yaml
go run ./cmd/issuer-server admin approve --config issuer.yaml --handle <hex>
```

For Docker-based deployments, see [`deploy/docker-compose.yml`](./deploy/docker-compose.yml).

## Storage

SQLite by default (zero-config, single-binary self-host). The
[`pgstore`](./pgstore/) submodule adds a Postgres backend for production
deployments — keep the default binaries free of the pgx dependency graph.

To use Postgres in production:

1. Provision a Postgres database.
2. Build a small wrapper that imports both this module and `pgstore`,
   instantiates `pgstore.NewProviderStore(pool)` /
   `pgstore.NewIssuerStore(ctx, pool, maxIndices)`, and wires it into
   `provider.Run` / `issuer.Run`. The
   `cmd/{provider,issuer}-server/main.go` files in this module are the
   reference implementation to adapt.
3. Apply DDL via `pgstore.Open(ctx, dsn)` on first boot — schema apply is
   guarded by a Postgres advisory lock and idempotent.

## Operator roles

| Audience | Reaches for | Why |
| --- | --- | --- |
| **Self-hoster** running their own Shadowname domain | `provider-server` | DNS TXT publishing + AgentCard hosting at `<your-domain>/identity/<local>`. |
| **Hub** vetting members (dating, hiring, professional society) | `issuer-server` in **keyed mode** | Self-serves an AgentCard, hosts CSR + status under the Hub's own `shadow://key:z6Mk...@host:port` identity. No DNS required. |
| **Organization** issuing employee/member credentials at a domain | `issuer-server` in **domain mode** | Uses `<your-domain>/.well-known/shadownet/issue` + `/status/<epoch>`. Lets sub-domains issue too (RFC 0001 §6.6). |
| **App/SDK developer** | `../python-sdk/` (PyPI: `shadownet`) | Receives/sends envelopes, fetches AgentCards, mints CSRs. |

## Subcommands

```
provider-server serve       --config <provider.yaml>
provider-server dns-record  --config <provider.yaml> [--issuer]
provider-server admin add    --config <yaml> --local <name> --pk <z6Mk...> --a2a-url <url>
provider-server admin remove --config <yaml> --local <name>
provider-server admin list   --config <yaml>

issuer-server serve  --config <issuer.yaml>
issuer-server admin approve      --config <yaml> --handle <hex>
issuer-server admin reject       --config <yaml> --handle <hex> [--reason "..."]
issuer-server admin revoke       --config <yaml> --epoch <n> --idx <n>
issuer-server admin rotate-epoch --config <yaml>
issuer-server admin list-pending --config <yaml> [--status new|approved|rejected]
```

## v0.1 users

The v0.1 protocol Go releases stay reachable on the Go proxy at
`core/v0.2.x`. The first v0.2-protocol Go release is tagged `core/v0.3.0`.

## License

MIT.
