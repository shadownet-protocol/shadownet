# integrations/

Distributable artifacts that wire host-agent ecosystems (Claude Code,
Hermes Agent, OpenClaw, plus raw skill bundles) to **any** Shadownet
Sidecar via the protocol's public surfaces. These are protocol-level
artifacts, not vendor-specific — the same plugin works against
[`hermes-social`](https://github.com/meghancampbel9/hermes-social)
self-hosts, hosted multi-tenant Sidecars, or any other RFC-compliant
Sidecar.

What "wire to a Sidecar" means concretely is two RFC-defined surfaces:

- **MCP endpoint** — `<sidecar-base>/u/<shadowname>/mcp` with
  `Authorization: Bearer <token>` (RFC-0007).
- **Webhook target** — registered via the `social_set_webhook` MCP tool;
  HMAC-SHA256 signed, dual-header
  (`X-Shadownet-Sidecar-Sig` + `X-Webhook-Signature`) (RFC-0007).

Neither is operator-specific. Hosts implementing RFC-0007 expose them at
the same paths.

## Layout

```
integrations/
├── PUBLISHING.md          how each artifact ships (npm, ClawHub, well-known, marketplace.json)
├── README.md              you are here
├── scripts/
│   └── sync_skills.py     materialise canonical SKILL.md files into both plugin trees
├── skills/                canonical agentskills.io-shape SKILL.md (dual-flavoured frontmatter)
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
| `plugins/hermes-agent/` | Hermes Agent (Nous Research) | a Sidecar's `/.well-known/skills/index.json` (some hosted Sidecars publish this for their tenants automatically) |
| `plugins/openclaw/` | OpenClaw | npm (`@shadownet/openclaw-plugin`) + ClawHub |

End-to-end publish guide: see [`PUBLISHING.md`](PUBLISHING.md).

## Configuration: the bundle endpoint is one option, not a requirement

Some hosted Sidecars expose a
`GET /v1/account/tenants/{id}/integration-bundle` endpoint as a
convenience: it returns a tenant's DID, shadowname, MCP endpoint,
tool/event names, and version in a single canonical payload. The plugin
installers can fetch that bundle to skip manual configuration.

This is **one** way to configure the integrations, not the way. Self-hosted
Sidecars and other operators can ship the same artifact (or its values
hand-rolled into the host agent's config). Everything in `plugins/` accepts
its configuration as plain values; the bundle endpoint is sugar.

## Skill sync

Skills are authored once at `skills/<name>/SKILL.md` with dual-flavoured
YAML frontmatter (top-level `description` / `allowed-tools` for Claude Code;
`metadata.hermes.*` for Hermes Agent). The sync script copies each canonical
file into both plugin trees:

```sh
python scripts/sync_skills.py        # materialise canonical → plugin trees
```

CI verifies there's no drift between `skills/<name>/SKILL.md` and the copies
under each plugin tree (see `.github/workflows/integrations.yml`).

## CI

`.github/workflows/integrations.yml` at the repo root:

- **OpenClaw plugin**: pnpm install + `lint` (tsc --noEmit) + `build` (tsup) +
  `test` (vitest). Runs on changes to `integrations/**`.
- **Manifest sanity**: every JSON / YAML / `*.snippet` file under
  `integrations/` is validated for parse-ability.
- **Skill bundle structure**: every `skills/*/` directory MUST contain a
  `SKILL.md` at its root.

A future `release-openclaw-plugin.yml` workflow (triggered by
`integrations/openclaw/v*` tags) will publish the OpenClaw plugin to npm
when its release cadence stabilizes; for now releases are manual.
