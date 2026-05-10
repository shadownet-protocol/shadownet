# Contributing

Thanks for considering a contribution. Shadownet covers two language SDKs
plus a protocol spec and a conformance suite (in sibling repos); pick the
surface that interests you and read the matching section.

## Layout

```
shadownet/
├── go/             Go reference implementation: SDK + reference SCA / SNS servers + operator CLI; pgstore/ submodule
├── py/             Python SDK (PyPI: shadownet)
└── (top level)     cross-cutting docs, CI, license, security policy
```

The two subtrees are intentionally not symmetrical. The Go subtree is the
protocol's reference implementation — it bundles library, server binaries,
and CLI in one Go module. The Python subtree is a client SDK port only.

Each subtree owns its own `CHANGELOG.md`, lockfile (`go.sum` / `uv.lock`),
linter config, and language-specific tooling. Cross-cutting concerns
(this guide, security policy, top-level README, repo-wide CI) live at the
repo root.

The protocol RFCs and JSON Schemas live in
[`shadownet-protocol/shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs);
the cross-implementation conformance suite lives in
[`shadownet-protocol/shadownet-conformance`](https://github.com/shadownet-protocol/shadownet-conformance).
Both are checked in CI on every PR here.

## Filing an issue

Use the templates under [`.github/ISSUE_TEMPLATE/`](./.github/ISSUE_TEMPLATE/).
Pick the right area label (`area: go-sdk`, `area: py-sdk`, `area: ci`,
`area: docs`) — it routes notifications.

For protocol-level questions or new RFC proposals, file against
[`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs)
instead.

For security issues, see [`SECURITY.md`](./SECURITY.md). Do **not** open a
public issue.

## Filing a pull request

1. Open or pick up an issue first for anything non-trivial. Discussion before
   code saves both sides time.
2. Branch from `main`. Keep PRs scoped to one logical change; split larger
   work into a series.
3. Don't co-mingle changes across SDKs in one PR unless you're touching a
   genuine cross-cutting concern (top-level CI, top-level docs).
4. Run the local pre-merge gate for the SDK you touched (below). Both gates
   run in CI on every PR; running them locally just gives faster feedback.
5. Commits should read as a coherent narrative. The maintainers don't enforce
   a strict convention, but please don't ship "wip" / "fix typo" / "address
   review" commits — squash before merging.

## Local pre-merge gate

### Go SDK — `go/`

```sh
cd go

# Main module
go build ./...
go test -race -count=1 ./...
go vet ./...
gofumpt -l -extra .            # must print nothing
staticcheck ./...
golangci-lint run              # uses .golangci.yml; v2.12+ recommended
govulncheck ./...
./tools/spdx-check.sh
./tools/check-schemas.sh       # checks api/ mirrors against ../shadownet-specs if present

# pgstore submodule (separate go.mod):
cd pgstore
go build ./...
go test -race -count=1 ./...
go vet ./...
# Integration tests are gated behind the `integration` build tag and need a
# Postgres at $SHADOWNET_TEST_PG_DSN; CI handles those.
```

Required toolchain: Go 1.25+; `gofumpt`, `staticcheck`, `golangci-lint`,
`govulncheck` available on `$PATH`.

### Python SDK — `py/`

```sh
cd py
uv sync --all-extras
uv run ruff check .
uv run ruff format --check .
uv run mypy src/shadownet         # strict
uv run pytest                     # full suite incl. conformance, with coverage
uv run pytest -m network          # opt-in: live-network tests
```

Required toolchain: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).
`mypy --strict` is enforced; ruff selects + ignores live in `pyproject.toml`.

The conformance tests look for a sibling `shadownet-specs` checkout; clone it
next to this repo or set `SHADOWNET_SPECS_PATH` to skip the lookup.

## Pre-commit hooks

A [`.pre-commit-config.yaml`](./.pre-commit-config.yaml) runs both ecosystems'
formatters and a generic hygiene pass on every commit. Set up once with:

```sh
pipx install pre-commit              # or: uv tool install pre-commit
pre-commit install
```

If you skip pre-commit, the same checks run in CI; they just give faster
feedback locally.

## Conformance and the wire

Wire-level cross-implementation interop is owned by `shadownet-conformance`,
which runs against this repo's reference servers and SDKs in CI. If you change
anything wire-visible (envelope shape, credential format, error-code naming,
SNS record schema), open a parallel PR against `shadownet-specs` (and a
fixture update) before merging here. The conformance job is a required check.

## Releases

Maintainers tag from `main`. Tags use the monorepo subtree-prefix scheme:

| Tag pattern | Triggers |
| --- | --- |
| `go/vX.Y.Z` | Go SDK release: cross-compiled CLI binaries + container images for `sca-server` / `sns-server` |
| `go/pgstore/vX.Y.Z` | pgstore submodule release: container images for `sca-server-pg` / `sns-server-pg` |
| `py/vX.Y.Z` | PyPI publish (Trusted Publishing) for the `shadownet` distribution |

Bump the matching `CHANGELOG.md`, ensure CI is green on `main`, then push the
tag. The release workflow handles the rest. Pre-releases use the matching
PEP-440 / semver suffixes (`py/v0.2.0-rc.1`, `go/v0.2.0-rc.1`).

## Code of Conduct

Participation in this project is governed by the
[Code of Conduct](./CODE_OF_CONDUCT.md). Report concerns privately via
[GitHub Security Advisories](https://github.com/shadownet-protocol/shadownet/security/advisories/new) —
the same channel handles both security disclosures and Code of Conduct
reports while the project is in early-stage solo-maintainer mode.
