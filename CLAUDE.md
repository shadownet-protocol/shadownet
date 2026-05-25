# CLAUDE.md

Cross-cutting conventions for the `shadownet` monorepo. Project overview is
in [README.md](./README.md); the protocol's normative source lives in a
sibling repo at [`../shadownet-specs/`](https://github.com/shadownet-protocol/shadownet-specs).
This file is for Claude (and any contributor) to know how the four subtrees
fit together and how to work across them.

For ecosystem-specific conventions, read the subtree's own `CLAUDE.md`:

| Subtree | Convention guide |
| --- | --- |
| `core/` | [`core/CLAUDE.md`](./core/CLAUDE.md) — Go SDK + reference servers + CLI + pgstore submodule |
| `python-sdk/` | [`python-sdk/CLAUDE.md`](./python-sdk/CLAUDE.md) — Python SDK (PyPI: `shadownet`) |
| `conformance/` | [`conformance/CLAUDE.md`](./conformance/CLAUDE.md) — cross-impl wire-level test suite |
| `integrations/` | — (host-agent plugins; per-plugin READMEs document specifics) |

## Spec authority

[`shadownet-protocol/shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs)
is the protocol's source of truth. Reading order for the v0.1 RFC set:
0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007.

- **If code and an RFC disagree, the RFC wins.**
- **If an RFC is silent or ambiguous, ask** (or open an RFC issue
  upstream). Do not silently invent semantics.
- Every wire artefact carries `"shadownet:v": "0.1"`; unknown versions
  are rejected at the boundary.
- Wire-level interop across implementations is owned by
  [`conformance/`](./conformance/). If a conformance test fails, **fix
  the implementation, not the test** — unless the spec changed, in
  which case land the spec PR first.

## Repository shape

```
shadownet/
├── core/              Go reference implementation: SDK (pkg/) + servers + CLI; Postgres backend in core/pgstore/
├── python-sdk/        Python SDK (PyPI: shadownet); consumed by shadownet-local and downstream Sidecars
├── conformance/       Cross-impl wire-level test suite (PyPI: shadownet-conformance + GHCR image + GitHub Action)
├── integrations/      Host-agent plugins (Claude Code, Hermes Agent, OpenClaw, raw skill bundles)
├── examples/          Per-language runnable end-to-end demos
├── .claude/skills/    Repo-scoped skills (e.g. `/release` — see below)
├── .github/           Workflows, issue templates, dependabot
├── CONTRIBUTING.md, SECURITY.md, MIGRATION.md, …
└── README.md, CLAUDE.md (this file)
```

Subtrees are **not symmetrical**: `core/` is a full reference implementation
(library + servers + CLI in one Go module); `python-sdk/` is a client SDK
port only. Naming reflects content, not language. Don't try to force them
into one shape.

## Cross-subtree dependencies

| From | To | Mechanism | Implication for releases |
| --- | --- | --- | --- |
| `core/pgstore/go.mod` | `core` (Go module proxy) | `require ... v0.X.Y` + in-repo `replace ../` for dev | `core/vX.Y.Z` MUST be on the Go proxy before tagging `core/pgstore/vX.Y.Z` |
| `conformance/pyproject.toml` | `python-sdk` (PyPI) | `shadownet>=0.2.0,<0.3` + `[tool.uv.sources]` `path = "../python-sdk"` for dev | `shadownet vX.Y.Z` MUST be on PyPI before tagging `conformance/vX.Y.Z` (the Docker build resolves from PyPI via `uv sync --no-sources`) |
| `conformance/fixtures/_regen/go-emit/go.mod` | `core` (Go module proxy) | `require ... + replace ../../../../core` | dev only — the binary is built on demand and never published |
| `shadownet-protocol/hermes-plugin` (satellite) | `shadownet-hermes-plugin` (PyPI, built from `integrations/plugins/hermes-agent/`) | `_VERSION_SPECIFIER = "~=X.Y.Z"` in `__init__.py` | PyPI patch flows transparently; PyPI minor/major requires a follow-up pin-bump + tag in the satellite repo. See `/release` skill → "Satellite repos". |

Topological release order: `python-sdk → conformance`, `core → core/pgstore`.
The two columns are independent.

## Branch + commit conventions

- Default branch: `main`. Never push to it directly without CI green.
- Never force-push `main`. Never amend a published tag.
- Commit-message style differs slightly per subtree (matches the legacy
  repos those subtrees came from):
  - `core/` and `core/pgstore/`: `scope: imperative summary` —
    `pkg/sca: add freshness handler`, `pgstore: pin parent module to vX.Y.Z`.
  - `python-sdk/`: descriptive sentence-case, often referencing an RFC
    section — `Add kid support to JWT minting for session tokens and
    subject-auth, relax URI constraints for method fields in RFC
    compliance`.
  - `conformance/`: terse, scope-prefixed —
    `sca: add csr_aud_mismatch test`.
  - Cross-cutting / CI / docs: use a `chore:` / `ci:` / `docs:` prefix or
    plain sentence-case.
- One logical change per commit; tests and code land together.
- **No `Co-Authored-By: Claude …` trailer in commits.** No AI co-authorship
  attribution anywhere in `main`.

## Releases

Use the `/release` skill at [`.claude/skills/release/SKILL.md`](./.claude/skills/release/SKILL.md).
It encodes the phased flow (prepare → release → verify) for all four
subtrees, knows the per-subtree commit-message style, runs the pre-flight
gates (CI on the bump commit, cross-subtree dependency-availability checks),
and captures every recovery dance we've learned the hard way.

Tag scheme:

- `core/vX.Y.Z` — Go SDK + reference binaries + multi-arch container images
  (`ghcr.io/shadownet-protocol/{sca,sns}-server`).
- `core/pgstore/vX.Y.Z` — Postgres backend submodule + `*-pg` images.
- `python-sdk/vX.Y.Z` — PyPI `shadownet` via Trusted Publishing.
- `conformance/vX.Y.Z` — `ghcr.io/shadownet-protocol/conformance` image
  (also what the published `shadownet-protocol/conformance-action@v0.X`
  consumes).
- `hermes-plugin/vX.Y.Z` — PyPI `shadownet-hermes-plugin` via Trusted
  Publishing. The Hermes Agent install shim at
  [`shadownet-protocol/hermes-plugin`](https://github.com/shadownet-protocol/hermes-plugin)
  (separate repo) pins this PyPI package with a compatible-release
  specifier; minor/major bumps require a follow-up shim release.

## CI/CD gotchas we've already paid for

These were caught during the 0.2.0 cut; if they recur, fix the workflow
rather than working around them in a release:

1. **Tag-strip patterns in `release-*.yml` workflows must match the tag
   prefix.** Lines like `${tag#core/}` and `${tag_full#python-sdk/v}` need
   updating in lockstep with any directory rename. Bash parameter
   expansions are easy to miss in a mechanical rename.
2. **Relative paths from `working-directory:` must be re-counted after any
   directory rename.** `release-core.yml` writes CLI tarballs to
   `../dist` (one level up from `core/`, i.e. the repo root). Before the
   rename it was `../../dist` (because the subtree was at `sdks/go/`).
3. **The conformance image build context is just `conformance/`.** The
   Dockerfile uses `uv sync --no-sources` so the in-repo
   `[tool.uv.sources]` override (which points `shadownet` at
   `../python-sdk`) is ignored at image-build time and resolves
   `shadownet` from PyPI like external consumers do.
4. **GHCR cross-repo package access** is per-package. Packages originally
   pushed from `shadownet-go` / `shadownet-conformance` need
   `shadownet-protocol/shadownet` granted "Write" access at
   `https://github.com/orgs/shadownet-protocol/packages/container/<name>/settings`
   → "Manage Actions access". Org-level Workflow Permissions is **not**
   enough on its own.
5. **`py.typed` is a literal filename**, not a path component. The
   `unzip -l ... | grep -q 'shadownet/py.typed'` verification step in
   `release-python-sdk.yml` and `python-sdk.yml` must stay exactly that.
   A naive rename pass over `py/` → `python-sdk/` will mangle it.

## Privacy / surface hygiene

- **`shadownet-cloud` is a private repo.** Don't reference its URL or
  internals in public-facing docs of this monorepo. Use generic phrasing
  like "downstream Sidecar deployments" or "a hosted cloud Sidecar"
  instead.
- The legacy `shadownet-go`, `shadownet-py`, and `shadownet-conformance`
  repos are public but inactive — new contributions and releases ship
  from this monorepo. Move-notice banners for those legacy repos live in
  `.consolidation-scratch/move-notices/` (gitignored).

## Maintainers

- @meghancampbel9
- @mahdi13

Both are listed in [`.github/CODEOWNERS`](./.github/CODEOWNERS) as default
reviewers.

## Things we do not do

- No mocks or placeholder implementations in shipped code anywhere across
  the four subtrees.
- No backwards-compatibility shims while the protocol is at v0.1.
- No emojis in code, comments, commits, or fixture file names.
- No banner comments or multi-paragraph docstrings. Per-language guides
  document the exact docstring style.
- No vendored deps. `go.sum` + `uv.lock` + `govulncheck` + Dependabot is
  the supply chain.
- No `Co-Authored-By: Claude …` trailers in commits — see "Branch + commit
  conventions" above.

## Working with this repo as Claude

1. **Identify the subtree** the change touches and read its CLAUDE.md
   before editing — coding conventions differ across ecosystems.
2. **Use `TaskCreate` / `TaskUpdate`** to track multi-step work
   proactively, especially across subtrees.
3. **Run the right gate** for the subtree before reporting work as
   complete:
   - `core/`: `go build ./... && go test -race -count=1 ./... && go vet
     ./... && gofumpt -l -extra . && staticcheck ./... && golangci-lint
     run && govulncheck ./... && ./tools/spdx-check.sh &&
     ./tools/check-schemas.sh` (run from `core/`).
   - `core/pgstore/`: same, from `core/pgstore/`. Integration tests need
     `SHADOWNET_TEST_PG_DSN` and skip silently when unset.
   - `python-sdk/`: `uv run ruff check . && uv run ruff format --check .
     && uv run mypy src/shadownet && uv run pytest` (from `python-sdk/`).
   - `conformance/`: same as python-sdk but from `conformance/`.
4. **For releases, defer to the `/release` skill.** Don't reinvent the
   phased flow ad-hoc.
5. **For consolidation history or "why is this set up this way"
   questions,** the answer is usually in
   [`MIGRATION.md`](./MIGRATION.md) and the commit history of `main`.
