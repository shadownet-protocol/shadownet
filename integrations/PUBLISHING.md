# Publishing Guide

How to ship the four integration artifacts to their respective channels. Each
section is self-contained — you can publish (or rebuild) any one without
touching the others.

## What gets published where

| Artifact | Destination | Trigger |
| --- | --- | --- |
| Claude Code plugin | A user-facing GitHub repo's `.claude-plugin/marketplace.json` (today: this repo's root) | tag `claude-plugin-vX.Y.Z` |
| Hermes Agent skill bundle | `https://app.sh4dow.org/.well-known/skills/index.json` (served by the cloud backend) | every backend deploy |
| OpenClaw plugin | npm (`@shadownet-protocol/openclaw-plugin`) **and** ClawHub | tag `openclaw-plugin-vX.Y.Z` |
| Skill bundle (canonical) | n/a — consumed by the three above | every commit, kept in sync via `make check-skills` |

The publishing pipelines never run from a developer's machine in production;
all four are wired into GitHub Actions in `.github/workflows/`. The commands
below are what those workflows execute, available locally for verification or
emergency manual publishes.

## Pre-publish checklist (any artifact)

Before tagging or merging a publish-triggering commit:

```sh
# Repo-wide checks every artifact depends on
make check-skills                              # canonical skills mirrored into both plugin trees

# Backend (the cloud serves the well-known/skills endpoint)
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -x

# Frontend (the connect page renders install snippets that name versions)
cd ../frontend && pnpm lint && pnpm test --run && pnpm build

# OpenClaw plugin (canonical TypeScript build)
cd ../integrations/plugins/openclaw && pnpm install && pnpm lint && pnpm test && pnpm build
```

If anything's red, do not publish. The CI workflow runs the same gates and is
the source of truth.

---

## Claude Code plugin

The plugin lives in `integrations/plugins/claude-code/` and is referenced from
the Claude Code marketplace catalog at the repo root: `.claude-plugin/marketplace.json`.

### Installing as a user

```text
/plugin marketplace add github:shadownet-protocol/shadownet
/plugin install shadownet@shadownet-protocol
```

The `github:owner/repo` form points Claude Code at the default branch's
`.claude-plugin/marketplace.json`. There is **no separate publish step** —
Claude Code reads directly from GitHub on `/plugin marketplace add` and
re-fetches when the user runs `/plugin marketplace update`.

### Releasing a new version

1. Bump `integrations/plugins/claude-code/.claude-plugin/plugin.json`'s `version`.
2. Bump the matching `version` field in `.claude-plugin/marketplace.json` →
   `plugins[0].version`.
3. Commit + tag: `git tag claude-plugin-v0.2.0 && git push origin claude-plugin-v0.2.0`.

Claude Code's update detection compares the value of `version` in `plugin.json`.
If you don't bump it, returning users won't see the update.

### Manual smoke test

```sh
claude --plugin-dir integrations/plugins/claude-code
# Then in Claude Code:
/help                                   # confirm /shadownet:* skills appear
/shadownet:shadownet-setup              # confirm MCP wiring
```

## Hermes Agent plugin

Ships as a real Python plugin to PyPI under the distribution name
`shadownet-hermes-plugin` (entry point group `hermes_agent.plugins`).
Users install via Hermes's documented plugin install command — no
well-known-URL skill installation step, no manual MCP config editing.

### How users install

```sh
hermes plugins install shadownet-protocol/shadownet --enable
```

Hermes prompts once for `SHADOWNET_TOKEN` (per the plugin's
`requires_env`), then loads `register(ctx)` at startup. The plugin
registers the four canonical skills via `ctx.register_skill` and a
`shadownet` platform adapter via `ctx.register_platform`. Inbound A2A
messages flow over the `social_inbox_wait` long-poll tool (RFC-0007
amendment D) — no `hermes webhook subscribe` step needed.

Alternative one-string install (RFC-0007 amendment B):

```sh
SHADOWNET_CONNECT_URL='shadownet://connect?base=https://app.sh4dow.org&token=...' \
  hermes plugins install shadownet-protocol/shadownet --enable
```

The plugin's `_resolve_config()` parses the connect URL and derives
both `SHADOWNET_TOKEN` and `SHADOWNET_SIDECAR_BASE_URL` from it.

### Updating the published plugin

1. Edit canonical SKILL.md files at `integrations/skills/<name>/SKILL.md`.
2. Run `make sync-skills` (or `python3 integrations/scripts/sync_skills.py`)
   to mirror them into the Claude Code + Hermes Agent plugin trees.
3. Bump `integrations/plugins/hermes-agent/pyproject.toml`'s `version`
   field (matches the `plugin.yaml` version).
4. Commit + tag: `git tag hermes-plugin-vX.Y.Z && git push origin
   hermes-plugin-vX.Y.Z`.
5. A `release-hermes-plugin.yml` GitHub Actions workflow publishes the
   wheel + sdist to PyPI via Trusted Publishing.

### Legacy skill bundle (pre-RFC-0007 amendments)

For users on sidecars that pre-date the amendments, the original
well-known install path remains supported as a fallback:

```sh
hermes skills install well-known:<base>/.well-known/skills/index.json
```

The cloud loads skills from the directory configured by
`SHADOWNET_CLOUD_INTEGRATIONS_DIR` (defaults to `<repo>/integrations`).
The well-known endpoint is served whenever the backend is deployed —
the well-known mechanism continues to work, but new installs should
prefer `hermes plugins install`.

## OpenClaw plugin (npm + ClawHub)

The plugin lives in `integrations/plugins/openclaw/` and ships as a real
TypeScript package under two channels:

- **npm**, as `@shadownet-protocol/openclaw-plugin`. The OpenClaw runtime resolves
  this when a user runs `openclaw plugins install clawhub:shadownet`
  (ClawHub fetches from npm under the hood).
- **ClawHub**, as the user-facing slug `shadownet`. ClawHub stores the
  uploaded `.tgz` for verification and surfaces the plugin in
  `clawhub search` results.

### Pre-publish

```sh
cd integrations/plugins/openclaw
pnpm install
pnpm lint                                # tsc --noEmit
pnpm test                                # vitest, 30 cases
pnpm build                               # tsup → dist/
```

The drift sentinel
(`backend/tests/integration/test_openclaw_plugin_drift.py`) must also pass —
it ensures the plugin's hardcoded TypeScript tool list matches the cloud's
`MCP_TOOL_NAMES`.

### Bumping the version

```sh
cd integrations/plugins/openclaw
# Edit package.json: "version": "0.X.Y"
# Edit openclaw.plugin.json if any configSchema fields changed
pnpm install --no-frozen-lockfile        # refresh lockfile metadata
```

Commit, then tag: `git tag openclaw-plugin-v0.X.Y && git push --tags`.

### npm publish

```sh
cd integrations/plugins/openclaw
pnpm build
# Smoke test the tarball before pushing:
pnpm pack --dry-run
# Publish:
NPM_TOKEN=$(op read 'op://shared/npm/token')   # or your secret manager
pnpm publish --access public --no-git-checks
```

The CI workflow at `.github/workflows/release-openclaw-plugin.yml` does this
on tag push, with the token sourced from the repo's `NPM_TOKEN` secret.

### ClawHub publish

After npm publish completes (so ClawHub's resolver can pull the tarball):

```sh
cd integrations/plugins/openclaw
clawhub login                            # opens browser for OAuth
clawhub package publish .                # dry-run + publish
```

ClawHub runs automated security scans (VirusTotal lookup, Gemini code
insight on unknown ZIPs) before listing publicly. Block-list outcomes
manifest as a status check on the published artifact; check
`clawhub package status shadownet` after publishing.

To pin a published version to "latest":

```sh
clawhub package tag shadownet latest 0.X.Y
```

### Smoke-testing post-publish

The Docker e2e harness validates the published artifact end-to-end without
needing OpenClaw on the developer's machine. Once the OpenClaw container
in `integrations/plugins/openclaw/deploy/compose.openclaw-test.yml` is wired
against a known-good image (currently a placeholder pending verification):

```sh
make test-openclaw-e2e                   # brings stack up, runs pytest, tears down
```

## Versioning

| Artifact | Versioning rule | Source |
| --- | --- | --- |
| Claude Code plugin | Manual bump in `plugin.json` + `marketplace.json` | `integrations/plugins/claude-code/.claude-plugin/plugin.json` |
| Hermes skill bundle | `version` per SKILL.md frontmatter; server hashes content for drift detection | `integrations/skills/<name>/SKILL.md` |
| OpenClaw plugin | Standard semver in `package.json` | `integrations/plugins/openclaw/package.json` |
| Backend (cloud) | `_version.py` + container tag — drives the well-known publisher | `backend/src/shadownet_cloud/_version.py` |

Tag conventions:

- `claude-plugin-vX.Y.Z`
- `openclaw-plugin-vX.Y.Z`
- `vX.Y.Z` for the backend (also tags a frontend release implicitly)

We deliberately do **not** unify versions across artifacts — the Claude Code
plugin and the OpenClaw plugin evolve independently, and forcing them to
move together creates pressure to ship coupled releases that don't need to
ship together.

## Rollback

- **Claude Code plugin**: revert the version bump on `main` and push. Users
  who installed the bad version see a downgrade warning on the next
  `/plugin marketplace update`. There is no "unpublish" — Claude Code reads
  GitHub directly.
- **Hermes Agent bundle**: revert on `main` and redeploy the backend. The
  next `hermes skills check` reports content drift; users opt into the
  rolled-back version with `hermes skills update`.
- **OpenClaw plugin (npm)**: `pnpm publish` cannot be undone within 24h
  without a security pretext. Publish a `0.X.Y+1` patch release that
  reverts the offending change instead.
- **OpenClaw plugin (ClawHub)**: `clawhub package unpublish shadownet
  --version 0.X.Y` removes the bad version from the listing. ClawHub
  notifies users on next `clawhub update --all`.

## Security

- **Never** commit credentials. The CI workflows source `NPM_TOKEN`,
  `CLAWHUB_TOKEN`, and `SHADOWNET_CLOUD_*` from GitHub Actions secrets.
- The OpenClaw plugin's webhook secret is generated server-side at the
  cloud's connect page and shown to the user once. Publishing the plugin
  does not handle user secrets.
- The Hermes Agent skill bundle is content-only — no secrets, no signed
  artifacts. Hermes content-hashes the SKILL.md bytes; the cloud serves the
  same hash in `/.well-known/skills/index.json`.

## Extracting `integrations/` to its own repo

The directory is self-contained:

- All plugin code, tests, scripts, deploy manifests, and qa harnesses live
  under `integrations/`.
- Cross-repo references that break on extraction:
  1. **Backend `Settings.integrations_dir`** — env-overridable (default
     `<repo>/integrations`). After extraction, the backend deploy points
     this at the new repo's checkout path or a mounted volume.
  2. **Backend drift sentinel** — `tests/integration/test_openclaw_plugin_drift.py`
     reads `<repo>/integrations/plugins/openclaw/src/tools/tools.ts`. After
     extraction, either git-submodule the integrations repo or mirror
     `MCP_TOOL_NAMES` into a JSON fixture the plugin commits, and have
     the sentinel read that fixture instead.
  3. **`.claude-plugin/marketplace.json`** — Claude Code spec mandates this
     path at the repo root that users add via `/plugin marketplace add github:owner/repo`.
     The marketplace catalog moves *with* the integrations to the new repo's
     root.
  4. **Frontend connect page** — references `https://app.sh4dow.org/.well-known/skills/index.json`
     (a URL on the cloud's domain). The cloud continues to serve that
     endpoint; only the source of truth for which skills get published
     moves out.

Everything else (`Makefile` targets, `tsconfig.json`, `pnpm-lock.yaml`,
`vitest.config.ts`, deploy/compose files, qa mocks) is contained inside
`integrations/` and travels cleanly. Extraction is a `git filter-repo` away.
