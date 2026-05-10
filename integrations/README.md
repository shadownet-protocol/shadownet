# integrations/

Distributable artifacts that wire `shadownet-cloud` into specific host-agent ecosystems. The
orchestrator's job is to publish a stable wire surface (RFC-0006 + RFC-0007 — MCP at
`/u/<shadowname>/mcp`, HMAC-signed webhooks). This directory packages that surface for
ecosystems whose users prefer a one-click install over copy-pasting config snippets.

| Subdirectory | Target | Status |
| --- | --- | --- |
| `skills/` | Skill bundle, agentskills.io shape — consumed by Claude Code, Hermes Agent | Phase B |
| `plugins/claude-code/` | Claude Code plugin (`.claude-plugin/`) + marketplace.json | Phase B |
| `plugins/hermes-agent/` | Hermes Agent skill bundle + `.well-known/skills/index.json` discoverability | Phase B |
| `plugins/openclaw/` | OpenClaw plugin (`openclaw.plugin.json` + `package.json`), v1 MCP-only, v2 channel plugin | Phase C–D |

Phase A (this commit) only scaffolds the directory tree. Implementation lands in subsequent
phases — see [`docs/integrations-roadmap.md`](../docs/integrations-roadmap.md) once it
exists, or the Phase A planning notes.

## Eventual extraction

These artifacts are repository-resident now for velocity. Once any one stabilises and starts
needing its own release cadence (versioning, changelog, marketplace submission), it moves to
its own repo under the `shadownet-protocol` GitHub org. Until then, keep changes in-tree to
avoid premature multi-repo overhead.

## Source-of-truth references

The artifacts here all consume shadownet-cloud's three public per-tenant surfaces:

- **MCP endpoint** — `<sidecar-base>/u/<shadowname>/mcp`, `Authorization: Bearer <token>`.
- **Webhook target** — registered via the dashboard or the `social_set_webhook` MCP tool;
  HMAC-SHA256 signed, dual-header (`X-Shadownet-Sidecar-Sig` + `X-Webhook-Signature`).
- **Integration bundle** — `GET /v1/account/tenants/{id}/integration-bundle` returns the
  canonical artifact set (DID, shadowname, endpoints, tool & event names, version) so per-
  ecosystem installers and snippet builders never duplicate strings.
