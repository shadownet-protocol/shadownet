# core/ — Claude conventions

Reference HTTP servers (Go) for Shadownet protocol v0.2. See the top-level
[CLAUDE.md](../CLAUDE.md) for cross-cutting monorepo conventions; this file
covers the `core/` subtree specifically.

## Role

`core/` ships **two binaries** and a Postgres-backend submodule. **No
public Go SDK.** All shared code lives under `internal/` and is an
implementation detail of the two binaries — never imported by external
consumers.

- `cmd/provider-server` — Shadowname Provider (RFC 0001 §5.2).
- `cmd/issuer-server` — Credential Issuer + status list (RFC 0001 §6.4–§6.5).
- `pgstore/` — separate Go module providing the Postgres `Store`
  implementations; opt-in.

If a future task asks "should we also expose X as a Go SDK?" — the answer
is **no** unless the user explicitly changes course. The canonical SDK is
`../python-sdk/`. See the project memory
`project_core_role_v02.md` for the strategic decision.

## When implementing an RFC

Per the root memory `feedback_read_rfc_first`: always fetch the official,
trusted source of any RFC/spec before writing code that depends on it.
Trusted sources for the specs this directory touches:

| Spec | URL |
| --- | --- |
| Shadownet v0.2 (this protocol) | <https://github.com/shadownet-protocol/shadownet-specs/tree/main/rfcs> |
| A2A v1.0 (Agent-to-Agent) | <https://a2a-protocol.org/> |
| RFC 8785 (JCS) | <https://www.rfc-editor.org/rfc/rfc8785> |
| RFC 8032 (Ed25519 / EdDSA) | <https://www.rfc-editor.org/rfc/rfc8032> |
| RFC 7515 (JWS) / RFC 7519 (JWT) | <https://www.rfc-editor.org/rfc/rfc7515> · <https://www.rfc-editor.org/rfc/rfc7519> |
| RFC 7807 (`application/problem+json`) | <https://www.rfc-editor.org/rfc/rfc7807> |
| RFC 1035 (DNS TXT, string chaining) | <https://www.rfc-editor.org/rfc/rfc1035> |
| Multibase / Multicodec | <https://github.com/multiformats/multibase> · <https://github.com/multiformats/multicodec> |

Local clones of the active reference repos live alongside the monorepo:
prefer reading from these over WebFetch when looking for spec or
reference-impl detail.

| Repo | Local path | Notable files |
| --- | --- | --- |
| A2A spec | `../../../dev/A2A` | `docs/specification.md`, `docs/topics/`, `specification/a2a.proto` |
| A2A Python SDK (cross-impl reference for AgentCard signing) | `../../../dev/a2a-python` | `src/a2a/utils/signing.py`, `src/a2a/types/` |
| Shadownet specs | `../../shadownet-specs` | `rfcs/0001-shadownet.md` + companions, `schemas/` |

Re-check upstream before every non-trivial change. No amount of reading is
"enough" — it never hurts to verify against HEAD.

## Module layout

| Path | Purpose |
| --- | --- |
| `cmd/{provider,issuer}-server/main.go` | Binary entry points. |
| `internal/crypto/`, `internal/httpx/`, `internal/keyguard/` | Substrate (Ed25519+JWS, HTTP server helpers, fixture-key guard). |
| `internal/jcs/` | RFC 8785 in-tree canonicalizer (cross-corpus tested against python-sdk's `shadownet.jcs`). |
| `internal/identifiers/` | Domain ∣ Shadowname ∣ MultibasePubKey union types. |
| `internal/wellknown/` | Named constants for wire paths, JWS `typ`, content types, headers. |
| `internal/agentcard/` | A2A §8.4 AgentCard build + JCS-sign + verify. |
| `internal/credential/` | `shadownet-cred+jwt` mint + verify. |
| `internal/csr/` | `shadownet-csr+jwt` parse + verify. |
| `internal/status/` | gzip+base64url bitstring (big-endian within byte). |
| `internal/provider/` | Provider server library (Record, Store, handler, etag, dns helpers). |
| `internal/provider/sqlitestore/` | Default SQLite Store implementation. |
| `internal/issuer/` | Issuer server library (Store, hook, epoch math, handlers for both modes). |
| `internal/issuer/hooks/{dev,queue}` | Ceremony Hook implementations. |
| `internal/issuer/sqlitestore/` | Default SQLite Store implementation. |
| `internal/cli/` | Operator subcommands (`keygen`, `inspect`). |
| `pgstore/` | Separate `go.mod` module; Postgres backend for both server stores. |
| `tools/` | `spdx-check.sh`, `check-schemas.sh`. |
| `deploy/`, `build/` | Docker compose example + per-binary Dockerfiles. |

## Conventions

- **No public Go API.** `pkg/` does not exist. Everything is `internal/`.
- **No third-party dep additions without justification.** The current dep
  set is intentional and small: `go-jose/v4`, `yaml.v3`,
  `modernc.org/sqlite` for the binaries; `pgx/v5` only in `pgstore/`.
  Specifically: **do not** swap our in-tree JCS / multibase / multicodec
  implementations for third-party libraries. Signature primitives stay
  in-tree so cross-impl interop with python-sdk is byte-exact and
  auditable.
- **Cross-impl signature primitives.** AgentCard signing, credential JWT
  mint, envelope canonicalization — anything that ends up in a signed
  byte stream — must produce output byte-identical to python-sdk's mirror
  for the same input. If you touch any of these, add a corresponding test
  vector to `conformance/fixtures/cross/`.
- **No `Co-Authored-By: Claude` trailer in commits.** Inherited from the
  root.
- **No banner comments / dashed section separators inside source files**
  (`# ----`, `# ====`, `# ***`). Function and class boundaries structure
  the file.
- **Lifecycle errors** wrap a typed sentinel (`provider.ErrNotFound`,
  `issuer.ErrInvalid`, etc.) so callers can `errors.Is` against a stable
  symbol.
- **Stdlib net/http** with Go 1.22 method-prefix routing
  (`mux.HandleFunc("GET /identity/{local}", ...)`). No external router.

## Local gate

Run from `core/`:

```sh
go build ./...
go test -race -count=1 ./...
go vet ./...
gofumpt -l -extra .
staticcheck ./...
golangci-lint run
govulncheck ./...
./tools/spdx-check.sh
./tools/check-schemas.sh
```

All eight must pass before pushing. From `core/pgstore/`:

```sh
go build ./...
go vet ./...
gofumpt -l -extra .
staticcheck ./...
golangci-lint run
SHADOWNET_TEST_PG_DSN=<dsn> go test -count=1 -tags integration ./...
```

The integration tests skip cleanly when `SHADOWNET_TEST_PG_DSN` is unset.

## Releases

See the `/release` skill at the root. Tag scheme:

| Tag | What it ships |
| --- | --- |
| `core/v0.3.x` | `provider-server` + `issuer-server` binaries, GHCR images. First v0.2-protocol release. |
| `core/pgstore/v0.3.x` | `ProviderStore` + `IssuerStore` Postgres backend. Released in lockstep with `core/`. |

The Go-module version and the protocol version don't have to align — be
explicit in the CHANGELOG which protocol revision a given Go release
implements.
