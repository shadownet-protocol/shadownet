# OpenClaw plugin

Target: [OpenClaw](https://docs.openclaw.ai/) — a general-purpose AI agent platform with a
multi-channel messaging gateway. OpenClaw plugins are JS modules registered via
`package.json`'s `openclaw.extensions` field plus an `openclaw.plugin.json` manifest
declaring contracts.

## Layout

```
openclaw/
├── package.json                  # name, version, openclaw.extensions, deps
├── openclaw.plugin.json          # id, name, contracts.tools, activation, configSchema
├── src/
│   ├── index.ts                  # plugin entry — registers with the api object
│   ├── mcp.ts                    # registers shadownet as an mcp.servers entry (v1)
│   └── channel.ts                # Shadownet-as-a-channel via createChatChannelPlugin (v2)
├── README.md
└── tests/
```

## Two phases of delivery

### v1 — MCP-only (Phase C)

The plugin reads `${SHADOWNET_TOKEN}` and `${SHADOWNET_ENDPOINT}` from the OpenClaw config
and registers the cloud's `/u/<shadowname>/mcp` endpoint as a remote HTTP MCP server. The
user's OpenClaw agent gains the full `social_*` tool inventory; Shadownet activity does NOT
yet appear as a chat channel inside OpenClaw.

### v2 — Channel plugin (Phase D)

Implements OpenClaw's `createChatChannelPlugin` SDK. Registers an HTTP route via
`api.registerHttpRoute()` as the cloud's webhook target. On `inbox.message` the plugin
calls `social_inbox` (via the registered MCP server) to fetch the actual message body,
then surfaces it through OpenClaw core as a channel message — so Shadownet appears
alongside Slack, Discord, iMessage, etc. in the user's OpenClaw gateway. Outbound user
replies route to `social_send` / `social_respond`.

## Distribution

Published to **ClawHub**, OpenClaw's plugin registry. Users install with:

```sh
openclaw plugins install clawhub:shadownet
openclaw gateway restart
```

## Phases

- v1 (MCP-only): Phase C
- v2 (channel plugin): Phase D

## References

- Plugin manifest: https://docs.openclaw.ai/plugins/plugin-manifest
- Building plugins: https://docs.openclaw.ai/plugins/building-plugins
- SDK overview: https://docs.openclaw.ai/plugins/sdk-overview
- Channel plugin SDK: https://docs.openclaw.ai/plugins/sdk-channel-plugins
- MCP CLI: https://docs.openclaw.ai/cli/mcp
- Hooks & automation: https://docs.openclaw.ai/automation/hooks
- ClawHub: https://docs.openclaw.ai/clawhub
