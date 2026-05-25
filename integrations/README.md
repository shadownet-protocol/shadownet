# integrations/

Distributable artifacts that wire host-agent ecosystems (Claude Code,
Hermes Agent, OpenClaw, plus raw skill bundles) to **any**
RFC-0007-compliant Shadownet Sidecar. These are protocol-level
artifacts, not vendor-specific — the same plugin works against
[`shadownet-local`](https://github.com/shadownet-protocol/shadownet-local)
self-hosts, hosted multi-tenant Sidecars, or any other RFC-compliant
Sidecar.

## Three transports, three inbound channels

A Shadownet integration has two directions: **outbound** (the agent
calls tools on the Sidecar) and **inbound** (the Sidecar delivers
events to the agent). Outbound is universal — every host uses MCP over
HTTP. Inbound varies by host capability:

| Inbound transport | Used by | Why |
| --- | --- | --- |
| `social_inbox_wait` MCP long-poll (RFC-0007 amendment D) | Hermes plugin, Claude Code monitor, any MCP-aware host on a user laptop | NAT-free — the client opens an outbound MCP connection and holds it open. Recommended for any host where the user runs the agent locally. |
| Webhook delivery (existing RFC-0007) | OpenClaw channel plugin, sidecar-to-sidecar, serverless functions, audit sinks | Stateless on the receiver side. Requires a publicly reachable HTTPS endpoint. Right for server-to-server. |
| `notifications/shadownet/*` MCP push (optional, RFC-0007 amendment D appendix) | TypeScript hosts whose MCP SDK supports `setNotificationHandler` (e.g. OpenClaw future opt-in) | Server-pushed; same connection as outbound tools. Not usable from the Python MCP SDK today due to a closed-union validation issue, so Python plugins use the long-poll tool instead. |

All three rails carry the same events with the same `event_id`, so a
receiver that bridges multiple transports can dedupe.

## What "wire to a Sidecar" means concretely

The RFC-0007 surfaces a plugin needs to know about:

- **MCP endpoint** — `<sidecar-base>/u/<shadowname>/mcp` with
  `Authorization: Bearer <token>` (RFC-0007).
- **`social_inbox_wait` MCP tool** — opt-in long-poll for inbound A2A
  messages (RFC-0007 amendment D).
- **Integration bundle endpoint** —
  `<base>/v1/account/me/integration-bundle` returns the per-tenant
  bootstrap payload (DID, shadowname, MCP endpoint, supported features,
  tool/event names, webhook secret) given just a bearer token
  (RFC-0007 amendment A).
- **Connect URL scheme** — `shadownet://connect?base=…&token=…` (inline)
  or `?handoff=<code>` (browser flow). One string the user pastes into
  any plugin to bootstrap (RFC-0007 amendment B).
- **`<base>/connect/<host>` install pages** — sidecar-served per-host
  install snippets, content-negotiated HTML or text (RFC-0007
  amendment C).

Hosts implementing RFC-0007 expose these at the same paths regardless
of operator.

## Layout

```
integrations/
├── PUBLISHING.md          how each artifact ships
├── README.md              you are here
├── scripts/
│   └── sync_skills.py     materialise canonical SKILL.md files into each plugin tree
├── skills/                canonical agentskills.io-shape SKILL.md (dual-flavoured frontmatter)
└── plugins/
    ├── claude-code/       Claude Code plugin — .mcp.json + skills/ + hooks/ + agents/ + monitors/
    │                      (monitors/inbound.py adds RFC-0007 amendment D long-poll inbound)
    ├── hermes-agent/      Hermes Agent plugin — plugin.yaml + pyproject.toml + register(ctx) +
    │                      ShadownetAdapter platform adapter; pip-distributed
    │                      (`pip install shadownet-hermes-plugin`)
    └── openclaw/          @shadownet-protocol/openclaw-plugin — TypeScript channel plugin + tools +
                           connect/ URL parser
```

## Per-host install paths (post-RFC-0007-amendments)

| Subdirectory | Target | One-token install command |
| --- | --- | --- |
| `plugins/hermes-agent/` | Hermes Agent (Nous Research) | `pip install shadownet-hermes-plugin` + paste-once `SHADOWNET_CONNECT_URL` from `<base>/connect/hermes-agent` into `~/.hermes/.env` |
| `plugins/claude-code/` | Claude Code (Anthropic) | `/plugin marketplace add github:shadownet-protocol/shadownet` then `/plugin install shadownet@shadownet-protocol` (optionally export `SHADOWNET_INBOUND=1` for real-time inbound) |
| `plugins/openclaw/` | OpenClaw | `openclaw plugins install clawhub:shadownet` |

For users on sidecars that pre-date the amendments, the legacy install
paths still work — see each plugin's `README.md` for the fallback.

## Configuration: the bundle endpoint and the connect URL

Two normative bootstrap mechanisms (both are RFC-0007 amendments A and
B; sidecars MUST implement them):

1. **Bundle fetch.** Given just a bearer token, a plugin calls
   `<base>/v1/account/me/integration-bundle` and receives the canonical
   payload — shadowname, MCP endpoint, supported features, tool/event
   names, webhook secret, version. Plugins use this to learn what the
   sidecar supports (`supported_features` flags include `mcp`,
   `webhook`, `inbox-wait`, `bundle`, `connect-url`,
   `mcp-notifications`).
2. **Connect URL.** The sidecar's account page mints a
   `shadownet://connect?base=…&token=…` URL. The user copies one
   string; the plugin's installer parses it (no separate base URL +
   token prompts).

A sidecar that pre-dates amendments returns 404 on the bundle endpoint;
plugins fall back to manual configuration with a clear error message.

## Skill sync

Skills are authored once at `skills/<name>/SKILL.md` with dual-flavoured
YAML frontmatter (top-level `description` / `allowed-tools` for Claude
Code; `metadata.hermes.*` for Hermes Agent). The sync script mirrors
the canonical file into each plugin tree:

```sh
python scripts/sync_skills.py        # materialise canonical → plugin trees
```

CI verifies there's no drift between `skills/<name>/SKILL.md` and the
copies under each plugin tree (see `.github/workflows/integrations.yml`).

## CI

`.github/workflows/integrations.yml` at the repo root:

- **OpenClaw plugin**: pnpm install + `lint` (tsc --noEmit) + `build`
  (tsup) + `test` (vitest). Runs on changes to `integrations/**`.
- **Hermes plugin**: `uv run ruff check` + `uv run mypy` + `uv run
  pytest` (adapter unit tests + Hermes-type shim).
- **Claude Code monitor**: `ruff check` + the monitor's own
  `test_inbound.py` test file.
- **Manifest sanity**: every JSON / YAML / `*.snippet` file under
  `integrations/` is validated for parse-ability.
- **Skill bundle structure**: every `skills/*/` directory MUST contain
  a `SKILL.md` at its root.
