# Shadownet plugin for Hermes Agent

Identity-anchored agent-to-agent communication via the [Shadownet
protocol](https://github.com/shadownet-protocol/shadownet-specs), packaged as a
real Hermes Agent plugin per the
[Hermes plugin docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins).

## What's in here

- **`plugin.yaml`** — Hermes plugin manifest. Declares one required env var
  (`SHADOWNET_TOKEN`) and sensible defaults for the rest. Read by Hermes
  only on the Git-clone install path; on the pip entry-point path
  Hermes never reads it (env-var validation is enforced at startup in
  `_adapter.check_shadownet_requirements`).
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

Visit your sidecar's `/connect/hermes-agent` page (the page mints your
account bearer token and hands you a paste-ready block). Inside your
Hermes environment — Docker container, host shell, wherever Hermes
runs — paste:

```sh
pip install shadownet-hermes-plugin
echo 'SHADOWNET_CONNECT_URL=shadownet://connect?base=<sidecar>&token=<minted>' >> ~/.hermes/.env
hermes gateway restart
```

`~/.hermes/.env` is read at startup (`python-dotenv` is in Hermes'
core deps). No shell `export`, no interactive prompts, no
`mcp_servers` YAML editing, no manual configuration. On startup
the plugin's `register(ctx)` registers the skills and the Shadownet
platform adapter; the adapter opens the outbound MCP session and
starts the long-poll loop.

### Configuration reference

The plugin reads its config from environment variables (or
`~/.hermes/.env`). One of `SHADOWNET_CONNECT_URL` or `SHADOWNET_TOKEN`
must be set; the rest have sensible defaults.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SHADOWNET_CONNECT_URL` | one of these | — | Full `shadownet://connect?base=…&token=…` URL — single value carries both base and token. What the sidecar's connect page hands you. |
| `SHADOWNET_TOKEN` | one of these | — | Account bearer token (use instead of `SHADOWNET_CONNECT_URL` if you prefer separate values). |
| `SHADOWNET_SIDECAR_BASE_URL` | no | `https://app.sh4dow.org` | Override for self-hosted sidecars (`shadownet-local`, internal deployments, …). Ignored when `SHADOWNET_CONNECT_URL` is set. |
| `SHADOWNET_LONG_POLL_TIMEOUT_SECONDS` | no | `30` | Per-call timeout for the inbox long-poll. Server clamps to ≤90s. |
| `SHADOWNET_NOTIFY_CHAT` | no | — | Cross-platform notification target for plan confirmations/invitations, format `platform:chat_id` (e.g. `telegram:123456`). When set, inbound coordination events are injected into this chat session so the user sees them and the agent has context for follow-up actions like `social_confirm_plan()`. Required for user-facing instances that participate in coordination flows. |

### Why pip, not `hermes plugins install`

`hermes plugins install owner/repo` (the Git-clone path) doesn't
resolve Python dependencies for the cloned tree. The adapter imports
`mcp.client.session` and the `shadownet` SDK transitively, so the
install would proceed but `register()` would fail at startup with
`ModuleNotFoundError`. The upstream Hermes plugin guide directs plugins
with third-party Python deps to distribute via pip — that's the
canonical path for our shape, and pip resolves all transitive deps
(`mcp`, `shadownet`, `httpx`, `pydantic`) automatically.

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
`shadownet-local`, hosted multi-tenant sidecars, internal self-hosts) by
setting `SHADOWNET_SIDECAR_BASE_URL`.

## Outbound tools

The plugin also lets Hermes invoke Shadownet's MCP tools (`social_send`,
`social_inbox`, `social_resolve`, `social_coordinate`, `social_confirm_plan`,
`social_accept_plan`, etc.). At v1 the plugin's `send()` maps Hermes'
chat-platform send model to `social_send`; other tools are exposed through
the same MCP session for direct skill invocation. Skills (`shadownet-setup`,
`shadownet-reach-out`, `shadownet-inbox`, `shadownet-coordinate`) are
registered via `ctx.register_skill` so `/skills/<name>` work out of the box.

## Updating

```sh
pip install --upgrade shadownet-hermes-plugin
hermes gateway restart
```

## License

MIT.
