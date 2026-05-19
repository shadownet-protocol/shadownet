---
name: release
description: Cut a release of one of the four monorepo subtrees (core, core/pgstore, python-sdk, conformance). Inspects commits since the last tag for that subtree, proposes a semver bump, drafts the CHANGELOG entry, walks the prepare / bump-commit / tag / publish phases in order, and never pushes without explicit approval. Use when the user says "release", "tag a new version", "cut python-sdk v0.2.1", "ship the core release", etc.
disable-model-invocation: true
argument-hint: <subtree> [version]
allowed-tools: Bash(git tag:*) Bash(git log:*) Bash(git diff:*) Bash(git status*) Bash(git show:*) Bash(git add:*) Bash(git commit:*) Bash(git describe:*) Bash(git rev-parse:*) Bash(git ls-files:*) Bash(git ls-remote:*) Bash(git remote:*) Bash(go list:*) Bash(go mod:*) Bash(go get:*) Bash(gh run:*) Bash(gh release:*) Bash(grep:*) Bash(find:*) Bash(cat:*) Bash(date:*) Bash(curl:*)
---

## Repository state

- Branch: !`git rev-parse --abbrev-ref HEAD`
- Working tree: !`git status --short`
- Origin URL: !`git remote get-url origin 2>/dev/null`
- Latest tag per subtree:
  - core:           !`git tag --list 'core/v*' --sort=-version:refname | grep -v 'pgstore' | head -1`
  - core/pgstore:   !`git tag --list 'core/pgstore/v*' --sort=-version:refname | head -1`
  - python-sdk:     !`git tag --list 'python-sdk/v*' --sort=-version:refname | head -1`
  - conformance:    !`git tag --list 'conformance/v*' --sort=-version:refname | head -1`

Pick the relevant subtree from `$ARGUMENTS` (first arg). If it's missing or
ambiguous, ask before doing anything else.

## Subtrees and their dependencies

| Subtree | Tag pattern | Publishes | Depends on |
| --- | --- | --- | --- |
| `core/` | `core/vX.Y.Z` | Go module on proxy · CLI tarballs on GitHub Release · `ghcr.io/shadownet-protocol/{sca,sns}-server` images | nothing |
| `core/pgstore/` | `core/pgstore/vX.Y.Z` | Go submodule on proxy · `ghcr.io/shadownet-protocol/{sca,sns}-server-pg` images | `core` (matching version must be on the Go proxy) |
| `python-sdk/` | `python-sdk/vX.Y.Z` | `shadownet` on PyPI via Trusted Publishing | nothing |
| `conformance/` | `conformance/vX.Y.Z` | `ghcr.io/shadownet-protocol/conformance` image; consumed by `shadownet-protocol/conformance-action@v0.X` | `python-sdk` (matching version must be on PyPI for the image build to resolve `shadownet`) |

Cross-subtree releases follow a topological order. For a coordinated `vN.0`
bump:

```
python-sdk → conformance
core → core/pgstore
```

The two columns are independent — you can ship Go-side before Python-side
or vice versa.

## Per-subtree file layout

| Subtree | Version source of truth | CHANGELOG |
| --- | --- | --- |
| `core/` | (no file; build-time stamp via `-ldflags "-X main.version=<tag>"`) | `core/CHANGELOG.md` |
| `core/pgstore/` | `core/pgstore/go.mod` (the `require` line pinning the parent) | shares `core/CHANGELOG.md` — pgstore release notes live there, in lockstep with main module |
| `python-sdk/` | `python-sdk/src/shadownet/_version.py` (hatch reads it via `dynamic = ["version"]`) | `python-sdk/CHANGELOG.md` |
| `conformance/` | `conformance/src/shadownet_conformance/_version.py` | `conformance/CHANGELOG.md` |

## Phases

Detect which phase we're in by reading the state above and the chosen
subtree:

| Phase | Trigger condition | Action |
|---|---|---|
| **0 — prepare** | unreleased commits in the subtree since its latest tag; CHANGELOG entry for the new version not present | draft CHANGELOG, propose version, run pre-flight checks |
| **1 — release** | CHANGELOG written for `<subtree>/vX.Y.Z`; bump commit pushed; CI green on the bump commit on `main`; tag doesn't exist on origin yet | give tag-and-push commands; watch release workflow |
| **2 — done** | tag exists on origin; release workflow green; artefacts verified | report and stop |

Don't skip phases. If the user asks "tag pgstore now" while `core` hasn't
been released yet — and pgstore's `require` line points at an unreleased
`core` version — refuse and explain that the parent must be released first
(see "Cross-subtree ordering" below).

## Phase 0 — prepare

Arguments: `<subtree>` (required), `<version>` (optional, you propose if
empty, e.g. `v0.2.1`).

1. **Sanity gates** (refuse if any fail, with one-line explanation):
   - Branch is `main`.
   - At least one commit in the subtree path since the latest tag for that
     subtree: `git log --oneline <latest-tag>..HEAD -- <subtree>/`.
   - If `<version>` is supplied, must be strictly greater than the latest
     tag for the subtree.
   - Working tree is clean except for files the user explicitly named for
     the release commit.
   - For cross-subtree dependencies (see table above), verify the
     dependency has its matching release available:
     - `core/pgstore` release → `core/vX.Y.Z` is on the Go proxy:
       `curl -sS https://proxy.golang.org/github.com/shadownet-protocol/shadownet/core/@v/<version>.info`
     - `conformance` release → `shadownet vX.Y.Z` is on PyPI:
       `curl -sS https://pypi.org/pypi/shadownet/<bare-version>/json | python3 -c "import sys,json; print(json.load(sys.stdin).get('info',{}).get('version','MISSING'))"`

2. **Categorize commits** in the subtree path into **Breaking / Added /
   Changed / Fixed / Operations**. One bullet per logical change, not per
   commit.

3. **Suggest the version**:
   - Pre-1.0: any Breaking → minor (`v0.1.x → v0.2.0`); else patch.
   - Post-1.0: Breaking → major; Added → minor; else patch.
   - Mention the alternative if it's defensible; let the user override.

4. **Draft the CHANGELOG entry** in the subtree's CHANGELOG, prepended
   under `## [Unreleased]`. Keep-a-Changelog format. Date today via
   !`date +%Y-%m-%d`. Skip empty categories. Add a reference link
   `[vX.Y.Z]: https://github.com/shadownet-protocol/shadownet/releases/tag/<subtree>%2Fv<X.Y.Z>`
   at the bottom (URL-encode the `/` in the tag).

5. **Verify the version-source-of-truth file** matches the proposed
   version:
   - `python-sdk`: edit `_version.py` to `X.Y.Z`.
   - `conformance`: edit `_version.py` to `X.Y.Z`.
   - `core`: no file edit; CLI gets stamped at build time.
   - `core/pgstore`: confirm `require github.com/shadownet-protocol/shadownet/core vX.Y.Z` already matches the latest `core` tag (this is the lockstep contract).

6. **Propose to user**:
   - The version.
   - Full CHANGELOG entry verbatim.
   - The exact commands Phase 1 will run.

   **Stop and wait for approval.** Anything ambiguous is a no.

## Phase 1 — release

After approval:

1. Write CHANGELOG + version-file edits.
2. Commit on `main` with the repo's existing commit-message style. For each
   subtree the style is:
   - `python-sdk`:  `Bump version to \`X.Y.Z\`: <one-line context>`
   - `core`:        `Update CHANGELOG for vX.Y.Z: <one-line context>`
   - `conformance`: `Release vX.Y.Z (conformance): <one-line context>`
   - `core/pgstore` rarely needs its own bump commit — its release commit is `pgstore: pin parent module to vX.Y.Z` only when bumping the parent require.

3. Push the commit:
   ```sh
   git add <files> && git commit -m '<message>' && git push origin main
   ```

4. **Watch the matching CI workflow on the bump commit**. Don't tag until
   it's green:
   ```sh
   gh run watch $(gh run list --branch main --workflow <subtree-workflow> --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   ```

   Workflow names per subtree:
   - `core`         → `core.yml`
   - `core/pgstore` → `core.yml` (the same workflow covers both modules via paths-filter)
   - `python-sdk`   → `python-sdk.yml`
   - `conformance`  → `conformance.yml`

5. **Tag and push the tag**:
   ```sh
   git tag -a <subtree>/vX.Y.Z -m '<subtree>/vX.Y.Z'
   git push origin <subtree>/vX.Y.Z
   ```

6. **Watch the matching release workflow**:
   - `core`         → `release-core.yml`         (CLI tarballs + 2 images)
   - `core/pgstore` → `release-core-pgstore.yml` (2 -pg images + GH Release)
   - `python-sdk`   → `release-python-sdk.yml`   (PyPI publish via Trusted Publishing)
   - `conformance`  → `release-conformance.yml`  (1 image + GH Release)

7. **Verify the published artefacts** (Phase 2 sanity):
   - Go module: `curl -sS https://proxy.golang.org/<module-path>/@v/<tag>.info`
   - PyPI: `curl -sS https://pypi.org/pypi/shadownet/<version>/json | jq -r '.info.version'`
   - GHCR images: anonymous manifest fetch (see "GHCR verification" in lessons below).
   - GitHub Release page exists at the new tag.

## Phase 2 — done

Print:
- The tag created.
- The published artefact URLs.
- Anything in `MIGRATION.md` or per-subtree `README.md` that mentions
  post-release work.

## Hard rules

- **Never push without showing the command and getting approval.** Even
  in autopilot mode, show what you're about to push and pause briefly.
- **Never amend a published tag.** Exception (per past precedent): if
  the release workflow failed *before* any artefact was uploaded, the
  tag may be deleted and re-pushed at the fix commit — but only with
  explicit user approval each time. PyPI uploads, GHCR pushes, and
  `gh release upload` are the "published" boundary; anything before
  is recoverable.
- **Never bypass `git commit` hooks.** No `--no-verify`.
- **Never `git push --force` to `main`.**
- **Refuse to skip a phase.** No tagging without a green CI on the bump
  commit. No phase-2 verification claims without actually fetching from
  the registry.
- **Match the repo's existing commit/tag-message style.** Check
  `git log --pretty=format:"%s" -5 -- <subtree>/` before writing your
  own.

## Cross-subtree ordering

- `core/pgstore` MUST be released after `core` because its `require` line
  pins a specific `core` version that must be on the Go proxy. Tag
  `core/vX.Y.Z` first; wait for the release workflow to publish (the Go
  proxy auto-indexes on first fetch within ~30–120s); then tag
  `core/pgstore/vX.Y.Z`.
- `conformance` MUST be released after `python-sdk` because the
  `release-conformance.yml` Docker build resolves `shadownet` from PyPI
  (the Dockerfile uses `uv sync --no-sources` to ignore the in-repo
  `[tool.uv.sources]` override).

## Lessons learned (the 0.2.0 release dance)

If any of these recur, fix them at the workflow level so they don't recur
again:

1. **Tag-strip patterns must match the tag prefix exactly.** Workflows
   that derive the bare version via `${tag#<prefix>/}` or
   `${tag_full#<prefix>/v}` need the prefix updated whenever a subtree
   directory is renamed. The 0.2.0 release caught three stale prefixes
   (`go/`, `py/v`, `go/pgstore/`) in one go.

2. **Relative paths from `working-directory:` must be re-counted after
   any directory rename.** The legacy Go subtree at `sdks/go/` used
   `../../dist` to land tarballs at the repo root; after the rename to
   `core/`, the correct path is `../dist`.

3. **The conformance Docker build context is `conformance/` only.**
   It doesn't have access to `../python-sdk`. The Dockerfile must use
   `uv sync --no-sources` so the local `[tool.uv.sources]` override is
   ignored at build time and `shadownet` resolves from PyPI.

4. **GHCR cross-repo package access.** When a package was originally
   pushed from another repo (e.g. the `sca-server` package was created
   by the legacy `shadownet-go` repo), the new `shadownet` repo needs
   per-package "Actions access" granted in the org's package settings —
   Workflow Permissions at the org level is *not* enough on its own.
   Manual one-time step: <https://github.com/orgs/shadownet-protocol/packages/container/<name>/settings>
   → "Manage Actions access" → Add `shadownet` with Role Write.

5. **GHCR verification without `gh` package scope.** Local `gh api` calls
   to list package versions fail with 403 unless your PAT has
   `read:packages`. Verify image presence anonymously instead:
   ```sh
   tok=$(curl -sS "https://ghcr.io/token?service=ghcr.io&scope=repository:shadownet-protocol/<image>:pull" \
           | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
   curl -sS -o /dev/null -w "%{http_code}\n" \
        -H "Authorization: Bearer $tok" \
        -H "Accept: application/vnd.oci.image.index.v1+json" \
        "https://ghcr.io/v2/shadownet-protocol/<image>/manifests/<tag>"
   ```
   A `200` confirms the image is pullable publicly.

6. **PyPI Trusted Publishing recovery.** If the verify-tag step gates the
   publish (which it does), a tag pointing at a broken commit means
   *nothing was uploaded to PyPI*. The tag can be deleted and re-pushed
   safely because PyPI is the only consumer-facing artefact and it
   never received anything. (Go module proxy is sticker; treat Go-side
   tags as immutable once any artefact is on the proxy.)

7. **`gh run rerun` doesn't always work on tag-triggered runs.** If
   `gh run rerun <id>` returns "This workflow run cannot be retried",
   delete the tag, delete the GitHub Release if one was created, then
   re-tag at the fix commit. Same recovery pattern as a fresh release,
   just with a tag-delete step at the front.

## Edge cases

- **No commits since last tag** → "nothing to release" and stop.
- **Uncommitted changes** → list them, ask whether to include in the
  release commit or stash. Do not silently include.
- **First release of a subtree** (no previous tag) → propose
  `v0.1.0` (or sync to the protocol version it implements) and treat
  all commits up to HEAD in that subtree as the first release.
- **Coordinated cross-subtree release** (e.g. all four to `v0.3.0`) →
  walk each release independently, in the topological order from
  "Subtrees and their dependencies." Don't try to atomic-tag.
- **Parent-tag CI fails after the tag is pushed** but artefacts are
  partially up → fix forward on `main`, cut a new patch, walk the
  phases again. Document the burned version in the next CHANGELOG
  entry. Per Lesson 6 above, `python-sdk` tags can be reset if no PyPI
  upload happened; `core`/`core/pgstore` tags should be treated as
  immutable once on the Go proxy.
- **Proxy lag on `go list -m -versions`** → 30–120s typical. Use
  `GOPROXY=direct go get ...@vX.Y.Z` as a fallback.
- **Stale Dependabot PRs after a rename** → close them by hand and let
  Dependabot regenerate against the current layout on its next sweep.

## Why phased

The Go module system makes multi-module releases inherently sequential:
a submodule's `require` line has to point at a real, published version
of its parent — and that version doesn't exist on the proxy until the
parent tag is pushed and indexed. There's no atomic "tag the whole
repo" command. The phases are the safety rails that keep the release
graph consistent at every public point. Same logic applies to
`conformance` consuming `shadownet` from PyPI.
