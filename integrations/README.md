# integrations/

Distributable artifacts that wire `shadownet-cloud` into specific host-agent ecosystems. The
orchestrator's job is to publish a stable wire surface (RFC-0006 + RFC-0007 — MCP at
`/u/<shadowname>/mcp`, HMAC-signed webhooks). This directory packages that surface for
ecosystems whose users prefer a one-click install over copy-pasting config snippets.

## Layout

```
integrations/
├── PUBLISHING.md          how each artifact ships (npm, ClawHub, well-known, marketplace.json)
├── README.md              you are here
├── scripts/
│   └── sync_skills.py     materialise canonical SKILL.md files into both plugin trees
├── skills/                canonical agentskills.io-shape SKILL.md (4 skills, dual-flavoured frontmatter)
└── plugins/
    ├── claude-code/       Claude Code plugin — .claude-plugin/plugin.json + .mcp.json + skills/ + hooks/ + agents/
    ├── hermes-agent/      Hermes Agent bundle — config.yaml.snippet + skills/ (synced from ../../skills/)
    └── openclaw/          @shadownet/openclaw-plugin — TypeScript channel plugin + tools
        ├── deploy/        Docker compose for the opt-in e2e harness
        ├── qa/            shadownet-mock FastAPI service feeding the e2e harness
        ├── src/           channel-plugin-api, webhook, messaging, runtime, account, security, tools
        └── tests/         vitest — 30 cases across client / tools / account / messaging / webhook
```

## Three artifacts, three publish channels

| Subdirectory | Target ecosystem | Publish channel |
| --- | --- | --- |
| `plugins/claude-code/` | Claude Code | the repo-root `.claude-plugin/marketplace.json` (users `/plugin marketplace add github:owner/repo`) |
| `plugins/hermes-agent/` | Hermes Agent (Nous Research) | served live by the cloud at `https://app.sh4dow.org/.well-known/skills/index.json` |
| `plugins/openclaw/` | OpenClaw | npm (`@shadownet/openclaw-plugin`) + ClawHub |

End-to-end publish guide: see [`PUBLISHING.md`](PUBLISHING.md).

## Source-of-truth references

The artifacts here all consume shadownet-cloud's three public per-tenant surfaces:

- **MCP endpoint** — `<sidecar-base>/u/<shadowname>/mcp`, `Authorization: Bearer <token>`.
- **Webhook target** — registered via the dashboard or the `social_set_webhook` MCP tool;
  HMAC-SHA256 signed, dual-header (`X-Shadownet-Sidecar-Sig` + `X-Webhook-Signature`).
- **Integration bundle** — `GET /v1/account/tenants/{id}/integration-bundle` returns the
  canonical artifact set (DID, shadowname, endpoints, tool & event names, version) so per-
  ecosystem installers and snippet builders never duplicate strings.

## Skill sync

Skills are authored once at `skills/<name>/SKILL.md` with dual-flavoured YAML frontmatter
(top-level `description` / `allowed-tools` for Claude Code; `metadata.hermes.*` for Hermes
Agent). The sync script copies each canonical file into both plugin trees:

```sh
make sync-skills        # materialise canonical → plugins
make check-skills       # CI: assert no drift
```

## Eventual extraction

The directory is structured to be extractable as a sibling repo once it earns its own
release cadence. Cross-repo seams are documented in [`PUBLISHING.md` §Extracting](PUBLISHING.md#extracting-integrations-to-its-own-repo).

Quick summary:

- **Self-contained**: `scripts/`, `qa/`, `deploy/`, `tests/`, lockfiles, docker compose
  manifests, vitest configs all live under `integrations/`.
- **Cross-repo seams (4 of them)**:
  1. `Settings.integrations_dir` (env-overridable in the cloud).
  2. The Python drift sentinel (`backend/tests/integration/test_openclaw_plugin_drift.py`)
     reads `integrations/plugins/openclaw/src/tools/tools.ts`. Becomes a submodule reference
     or a JSON fixture after extraction.
  3. `.claude-plugin/marketplace.json` lives at the repo root because the Claude Code
     `/plugin marketplace add github:owner/repo` form expects it there. Travels with the
     integrations to the new repo's root.
  4. The frontend connect page references the cloud's `/.well-known/skills/index.json` URL
     by host; only the source of truth for which skills get published moves out.

## CI

Each artifact has its own GitHub Actions workflow gated by `paths` filters:

- `.github/workflows/openclaw-plugin.yml` — lint + test + build the OpenClaw plugin
- `.github/workflows/integrations.yml` — `check-skills` + drift sentinel
- `.github/workflows/release-openclaw-plugin.yml` — npm + ClawHub publish on tag push
- `.github/workflows/openclaw-e2e.yml` — opt-in Docker harness (workflow_dispatch)

The existing `ci.yml` covers backend + frontend independently.
