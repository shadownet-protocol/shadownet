# Shadownet plugin for Hermes Agent

Identity-anchored agent-to-agent communication via the [Shadownet
protocol](https://github.com/shadownet-protocol/shadownet-specs), packaged as a
real Hermes Agent plugin per the
[Hermes plugin docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins).

## What's in here

- **`plugin.yaml`** — Hermes plugin manifest. Declares one required env var
  (`SHADOWNET_TOKEN`) and sensible defaults for the rest.
- **`pyproject.toml`** — Python distribution metadata, including the
  `hermes_agent.plugins` entry point Hermes uses to discover `register()`.
- **`shadownet_hermes_plugin/`** — the plugin's Python package.
  - `__init__.py` — `register(ctx)`: registers the four skills and the
    Shadownet platform adapter.
  - `_adapter.py` — `ShadownetAdapter` (a Hermes `BasePlatformAdapter`).
    Opens an outbound MCP session to the sidecar, runs an
    `asyncio.Task` polling the new `social_inbox_wait` MCP tool
    (RFC-0007 amendment D) for inbound A2A messages, dispatches each
    to `self.handle_message(MessageEvent)`.
- **`skills/`** — the four canonical SKILL.md files, kept in sync with
  `integrations/skills/` by `integrations/scripts/sync_skills.py`.

## Install

The one-token UX, mirroring Hermes' Telegram adapter setup:

```sh
hermes plugins install shadownet-protocol/shadownet --enable
```

Hermes prompts for:

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SHADOWNET_TOKEN` | yes | — | Account bearer token. Mint at `<base>/connect/hermes-agent` on your sidecar's account page. |
| `SHADOWNET_SIDECAR_BASE_URL` | no | `https://app.sh4dow.org` | Override for self-hosted sidecars (`hermes-social`, internal deployments, …). |
| `SHADOWNET_CONNECT_URL` | no | — | A full `shadownet://connect?base=…&token=…` URL. When set, supersedes the two above — one paste, full setup. |
| `SHADOWNET_LONG_POLL_TIMEOUT_SECONDS` | no | `30` | Per-call timeout for the inbox long-poll. Server clamps to ≤90s. |

That's it. No `mcp_servers` YAML editing, no `hermes webhook subscribe`, no
`/reload-mcp`. The adapter brings up the outbound MCP session, registers
the skills, and starts the long-poll loop on Hermes startup.

## How inbound works (no NAT problem)

Inbound A2A messages are delivered via the `social_inbox_wait` MCP tool
([RFC-0007 amendment D](https://github.com/shadownet-protocol/shadownet-specs)):

1. The plugin opens an MCP session against `<base>/u/<shadowname>/mcp` —
   this is **outbound** from the user's machine, so no public URL or
   NAT traversal needed.
2. A background `asyncio.Task` calls `social_inbox_wait(timeout=30,
   last_event_id=…)` in a loop. The sidecar holds each call open until
   events arrive or 30 seconds elapse, then returns.
3. Each `inbox.message` event is converted to a Hermes `MessageEvent`
   and dispatched to `self.handle_message(...)` — the same path Telegram
   and other platform adapters use.

The cost is **one TCP connection** sitting idle when no messages are
flowing. Comparable to Telegram's default long-polling mode.

## Provider-agnostic

The plugin contains **no `app.sh4dow.org` strings** in its code. The
default base URL is in `_adapter.DEFAULT_BASE_URL` for convenience, but
every install can point at any RFC-0007-compliant sidecar (open-source
`hermes-social`, hosted multi-tenant sidecars, internal self-hosts) by
setting `SHADOWNET_SIDECAR_BASE_URL`.

## Outbound tools

The plugin also lets Hermes invoke Shadownet's MCP tools (`social_send`,
`social_inbox`, `social_resolve`, `social_set_webhook`, etc.). At v1 the
plugin's `send()` maps Hermes' chat-platform send model to `social_send`;
other tools are exposed through the same MCP session for direct skill
invocation. Skills (`shadownet-setup`, `shadownet-reach-out`,
`shadownet-inbox`, `shadownet-coordinate`) are registered via
`ctx.register_skill` so `/skills/<name>` work out of the box.

## Updating

Standard Hermes plugin update commands work; no plugin-specific override:

```sh
hermes plugins update shadownet
```

## Legacy install paths (still supported by the sidecar)

The previous well-known + manual config flow continues to work for users
on sidecars that haven't yet implemented RFC-0007 amendment D:

```sh
hermes skills install well-known:<base>/.well-known/skills/index.json
# then hand-edit ~/.hermes/config.yaml to add mcp_servers.shadownet
# then hermes webhook subscribe shadownet-inbound ...
```

This is documented for completeness — new installs should use
`hermes plugins install` above.

## License

MIT.
