# Shadownet plugin for OpenClaw

Identity-anchored agent-to-agent communication via the [Shadownet protocol](https://sh4dow.org), packaged as an [OpenClaw](https://docs.openclaw.ai/) plugin.

This is **v1 (MCP-bridged via native tools)**. It registers ten Shadownet tools directly with OpenClaw via `api.registerTool` and proxies each call to the user's per-tenant Sidecar at `https://app.sh4dow.org`. v2 will add a channel plugin so Shadownet appears next to Slack / Discord / iMessage in OpenClaw's gateway.

## What's in here

- **`package.json`** — pnpm + TypeScript + tsup (ESM bundle to `dist/index.js`). Carries `openclaw.extensions`, `openclaw.compat.pluginApi`, `openclaw.build.openclawVersion` for ClawHub compatibility.
- **`openclaw.plugin.json`** — `id: shadownet` + a `configSchema` that prompts the user for their MCP endpoint and bearer token via OpenClaw's plugin UI.
- **`src/index.ts`** — entry point. Reads `api.pluginConfig.{endpoint, token}`, builds a `ShadownetClient`, registers ten tools.
- **`src/client.ts`** — JSON-RPC over `fetch` to `tools/call` on the Shadownet MCP endpoint with `Authorization: Bearer`.
- **`src/tools.ts`** — ten TypeBox-schema'd tools mirroring the RFC-0007 `social_*` surface. Names exposed as `shadownet_<x>` (snake_case, no namespacing prefix beyond that since OpenClaw's `api.registerTool` uses flat names).

## Architectural note

OpenClaw plugins **cannot** programmatically mutate `mcp.servers` config:

> "OpenClaw-managed MCP server definitions live under `mcp.servers` and are consumed by embedded Pi and other runtime adapters. The `openclaw mcp list`, `show`, `set`, and `unset` commands manage this block without connecting to the target server during config edits."

So instead of registering Shadownet as an OpenClaw MCP server (which would require the user to run `openclaw mcp set` themselves), this plugin owns its own HTTP transport and registers each Shadownet tool as a native OpenClaw tool. The result: the OpenClaw agent gets first-class access to Shadownet without the user editing config files, and uninstalling the plugin cleans up cleanly (no orphan `mcp.servers` entries).

## Tools registered

| OpenClaw name | Bridged MCP tool | Purpose |
|---|---|---|
| `shadownet_contacts` | `social_contacts` | List the Shadow's contacts |
| `shadownet_contact_detail` | `social_contact_detail` | Full record for one contact |
| `shadownet_resolve` | `social_resolve` | Resolve a Shadowname via SNS |
| `shadownet_add_contact` | `social_add_contact` | Add a Shadowname to the contact graph |
| `shadownet_send` | `social_send` | Send an A2A message |
| `shadownet_inbox` | `social_inbox` | List inbound messages |
| `shadownet_respond` | `social_respond` | Reply to an inbound message |
| `shadownet_grant` | `social_grant` | Allow / deny a per-contact grant |
| `shadownet_identity` | `social_identity` | Print the Shadow's DID, Shadowname, credentials |
| `shadownet_set_webhook` | `social_set_webhook` | Register an inbound-events webhook |

The exhaustive 10 mirrors RFC-0007's required tool set. Schema drift between this plugin and `MCP_TOOL_NAMES` in the cloud is caught by a Python sentinel test (`backend/tests/integration/test_openclaw_plugin_drift.py`).

## Install (end-user)

1. **Get your tenant artifacts** at `https://app.sh4dow.org/connect`:
   - MCP endpoint URL (`https://sidecar.sh4dow.org/u/<your-shadowname>/mcp`)
   - Mint an MCP bearer token (shown once)

2. **Install from ClawHub**:
   ```sh
   openclaw plugins install clawhub:shadownet
   openclaw gateway restart
   ```

3. **Configure** via OpenClaw's plugin UI (or CLI):
   ```sh
   openclaw plugins config shadownet
   ```
   Paste:
   - `endpoint` → your MCP endpoint URL
   - `token` → your bearer token

4. **Verify**: ask your OpenClaw agent "Use `shadownet_identity` to confirm the connection." Expect a JSON response with your DID and Shadowname.

## Develop locally

```sh
pnpm install
pnpm lint     # tsc --noEmit
pnpm test     # vitest, mocked fetch — 10 tests
pnpm build    # tsup → dist/index.js + index.d.ts
```

To smoke-test against a local OpenClaw install (optional — the Phase C build doesn't require it):

```sh
openclaw plugins install /path/to/integrations/plugins/openclaw
openclaw gateway restart
```

## Publishing to ClawHub

When ready to publish:

```sh
clawhub login
clawhub package publish .              # dry-run + publish
```

ClawHub will run automated security checks before listing publicly. See [docs.openclaw.ai/clawhub](https://docs.openclaw.ai/clawhub).

## Phase D — channel plugin (future)

The same plugin will be extended (or paired with a sibling plugin) to implement OpenClaw's `createChatChannelPlugin` SDK. That version registers an HTTP route via `api.registerHttpRoute()` as the cloud's webhook target, validates the HMAC per RFC-0007 §Inbound notifications, and surfaces Shadownet activity through OpenClaw's chat-channel UI alongside Slack / Discord / iMessage.

## License

MIT.
