# Contributing

Thanks for considering a contribution. Shadownet covers two language SDKs
plus a protocol spec and a conformance suite (in sibling repos); pick the
surface that interests you and read the matching section.

## Layout

```
shadownet/
├── core/             Go reference implementation: SDK + reference SCA / SNS servers + operator CLI; pgstore/ submodule
├── python-sdk/             Python SDK (PyPI: shadownet)
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
[`shadownet-protocol/shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs)
(separate repo; pinned by ref in `.github/workflows/conformance.yml`). The
cross-implementation conformance suite lives in `conformance/` here, runs in
CI on every PR, and ships as both a PyPI distribution
(`shadownet-conformance`) and a Docker GitHub Action
(`shadownet-protocol/conformance-action@v0.1`).

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

### Go SDK — `core/`

```sh
cd core

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

### Python SDK — `python-sdk/`

```sh
cd python-sdk
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

### Conformance suite — `conformance/`

```sh
cd conformance
uv sync --all-extras
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                     # unit tests only — network tests skip without --target

# Live run against the in-repo Go reference servers (separate terminals):
( cd ../core
  go build -o /tmp/sca-server ./cmd/sca-server
  go build -o /tmp/sns-server ./cmd/sns-server
  go build -o /tmp/shadownet  ./cmd/shadownet )
mkdir -p /tmp/sn-self
/tmp/shadownet keygen --out /tmp/sn-self/issuer.jwk
/tmp/shadownet keygen --out /tmp/sn-self/provider.jwk
SHADOWNET_ALLOW_INSTANT_APPROVAL=1 /tmp/sca-server -config ci/sca-server.yaml &
/tmp/sns-server -config ci/sns-server.yaml &
uv run shadownet-conformance \
  --target sca=http://127.0.0.1:18443 \
  --target sns=http://127.0.0.1:18444 \
  --proof-method instant-approval
```

Required toolchain: Python 3.12+, `uv`, Go 1.25+ (only for the fixture
regen tool — the suite itself doesn't need Go). The conformance suite
consumes the in-repo Python SDK via `[tool.uv.sources]` in
`conformance/pyproject.toml`.

### Integrations — `integrations/`

The OpenClaw plugin is the only TypeScript artefact that needs build /
test tooling:

```sh
cd integrations/plugins/openclaw
pnpm install --frozen-lockfile
pnpm run lint                     # tsc --noEmit
pnpm run build                    # tsup
pnpm run test                     # vitest
```

The other integrations (Claude Code plugin, Hermes Agent skills, raw
skill bundles) are config-only — no build step. CI verifies that every
JSON / YAML manifest parses and that every `skills/<name>/` directory
contains a `SKILL.md` (the agentskills.io shape). Required toolchain:
Node 20+ and pnpm 9+.

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

Wire-level cross-implementation interop is owned by the suite at
[`conformance/`](./conformance/), which runs against this repo's reference
servers and SDKs in CI. If you change anything wire-visible (envelope shape,
credential format, error-code naming, SNS record schema), open a parallel PR
against
[`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs)
(and a fixture update) before merging here. The conformance job is a required
check.

Rule of thumb for conformance test failures: **fix the implementation, not
the test, unless the spec changed.** The suite is the contract; if it
disagrees with an SDK, the SDK is wrong by definition. The exception is
spec-driven changes — those land in `shadownet-specs` first, then both the
fixtures and any affected SDK update in lockstep.

## Releases

Maintainers tag from `main`. Tags use the monorepo subtree-prefix scheme:

| Tag pattern | Triggers |
| --- | --- |
| `core/vX.Y.Z` | Go SDK release: cross-compiled CLI binaries + container images for `sca-server` / `sns-server` |
| `core/pgstore/vX.Y.Z` | pgstore submodule release: container images for `sca-server-pg` / `sns-server-pg` |
| `python-sdk/vX.Y.Z` | PyPI publish (Trusted Publishing) for the `shadownet` distribution |
| `conformance/vX.Y.Z` | PyPI publish for `shadownet-conformance` + multi-arch image push to `ghcr.io/shadownet-protocol/conformance` (consumed by `shadownet-protocol/conformance-action@v0.X`) |

Bump the matching `CHANGELOG.md`, ensure CI is green on `main`, then push the
tag. The release workflow handles the rest. Pre-releases use the matching
PEP-440 / semver suffixes (`python-sdk/v0.2.0-rc.1`, `core/v0.2.0-rc.1`,
`conformance/v0.2.0-rc.1`).

Integrations releases (e.g. publishing `@shadownet/openclaw-plugin` to npm)
are currently manual; an automated `release-openclaw-plugin.yml` workflow
will land once the OpenClaw plugin's release cadence stabilizes.

## Code of Conduct

Participation in this project is governed by the
[Code of Conduct](./CODE_OF_CONDUCT.md). Report concerns privately via
[GitHub Security Advisories](https://github.com/shadownet-protocol/shadownet/security/advisories/new) —
the same channel handles both security disclosures and Code of Conduct
reports while the project is in early-stage solo-maintainer mode.
