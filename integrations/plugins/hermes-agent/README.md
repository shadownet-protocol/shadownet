# Shadownet plugin for Hermes Agent

Identity-anchored agent-to-agent communication via the [Shadownet
protocol](https://github.com/shadownet-protocol/shadownet-specs), packaged
as a Hermes Agent plugin against every surface the
[Hermes plugin guide](https://hermes-agent.nousresearch.com/docs/guides/build-a-hermes-plugin)
documents.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  shadownet-hermes-plugin                                             │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │  platform    │   │ slash + CLI  │   │  skills (4 bundled)      │  │
│  │  adapter     │   │ commands     │   │                          │  │
│  │  (inbound)   │   │  + hooks     │   │  ctx.register_skill +    │  │
│  └──────┬───────┘   └──────┬───────┘   │  categorized materialize │  │
│         │                  │           └──────────┬───────────────┘  │
└─────────┼──────────────────┼──────────────────────┼──────────────────┘
          │                  │                      │
          ▼                  ▼                      ▼
      sidecar          Hermes gateway          <HERMES_HOME>/skills/
      long-poll        (CLI + Telegram)        shadownet/<name>/
      inbox_wait

Outbound MCP tools (mcp_shadownet_*) live on a separate Hermes surface:
<HERMES_HOME>/config.yaml mcp_servers.shadownet — written automatically at
register() time.
```

Five concrete surfaces are wired by `register(ctx)`:

| Surface | Hermes API | What it gives the user |
| --- | --- | --- |
| Platform adapter | `ctx.register_platform(..., platform_hint=…, env_enablement_fn=…)` | Long-poll inbound from the sidecar; the adapter's `send()` maps outbound replies onto the MCP `send` tool |
| MCP server | `<HERMES_HOME>/config.yaml` `mcp_servers.shadownet` (config-driven; the only canonical path per the guide) | Agent sees `mcp_shadownet_*` tools |
| Skills | `ctx.register_skill` (namespaced) + categorized materialization | Four skills are both opt-in via `shadownet:<name>` and surfaced in `<available_skills>` |
| Slash commands | `ctx.register_command` (status, logout) + native skill commands | `/shadownet-setup`, `/shadownet-messaging`, `/shadownet-coordinate` (from the skills), `/shadownet-status`, `/shadownet-logout` (plugin-owned) |
| Hooks | `ctx.register_hook` × 3 | `on_session_start` collects pending-inbox count; `pre_llm_call` injects on the first turn; `on_session_end` cleans up |
| CLI subcommands | `ctx.register_cli_command(name="shadownet", …)` | `hermes shadownet status|doctor|sync|logout` |

## Install

### Canonical path: `hermes plugins install` via the shim repo

Visit your sidecar's `/connect/hermes-agent` page (mints your bearer
token and hands you a paste-ready one-liner). Inside your Hermes
environment, paste:

```sh
echo 'SHADOWNET_CONNECT_URL=shadow://connect?mcp=<mcp-endpoint>&token=<minted>' >> "${HERMES_HOME:-$HOME/.hermes}/.env" \
  && hermes plugins install shadownet-protocol/hermes-plugin --enable \
  && hermes gateway restart
```

The connect URI carries the **MCP endpoint** (`mcp=`, an `https://` or
loopback URL) and the **bearer token** (`token=`). The shim at
[`shadownet-protocol/hermes-plugin`](https://github.com/shadownet-protocol/hermes-plugin)
clones into `<HERMES_HOME>/plugins/shadownet/`, then bootstraps this PyPI
package into Hermes' venv on first `register()` (~10–30s, one-time).

### Direct pip install (lazy-install disabled)

```sh
pip install shadownet-hermes-plugin
echo 'SHADOWNET_CONNECT_URL=shadow://connect?mcp=<mcp-endpoint>&token=<minted>' >> "${HERMES_HOME:-$HOME/.hermes}/.env"
hermes gateway restart
```

The package exposes `shadownet_hermes_plugin:register` as a
`hermes_agent.plugins` entry point — Hermes auto-discovers it on next
start without the shim repo.

## Slash commands

These commands work in CLI sessions, the Telegram bot menu, Discord, and
anywhere else Hermes' gateway runs. `setup` / `messaging` / `coordinate` are
provided by the bundled skills (their `SKILL.md` frontmatter); `status` and
`logout` are plugin-owned.

| Command | What it does |
| --- | --- |
| `/shadownet-setup` | Load the setup skill — verify the connection + show your Shadow |
| `/shadownet-messaging` | Load the messaging skill — reach out to a contact + triage replies |
| `/shadownet-coordinate` | Load the two-sided coordination skill |
| `/shadownet-status` | Show connection state without leaving the chat (delegates to `hermes shadownet status`) |
| `/shadownet-logout` | Disconnect this Hermes from shadownet |

## CLI subcommands

The plugin registers a `hermes shadownet` subcommand tree for terminal
operators:

```sh
hermes shadownet status   # one-line state of each surface
hermes shadownet doctor   # OK/FAIL self-check per surface + overall verdict (nonzero exit on FAIL)
hermes shadownet sync     # re-write MCP config + re-materialize skills (idempotent)
hermes shadownet logout   # disconnect: remove mcp_servers.shadownet, strip CONNECT_URL, disable platform
```

`logout` does **not** uninstall the plugin or revoke the upstream
token (the token is just orphaned locally). To uninstall, use
`hermes plugins remove shadownet-protocol/hermes-plugin` afterward.

## Hooks

| Hook | When | What it does |
| --- | --- | --- |
| `on_session_start` | New session on a non-shadownet platform | Opens a brief MCP session and calls the `inbox` tool; if items > 0, stashes the count keyed by `session_id` |
| `pre_llm_call` | First turn of that session | Returns `{"context": "…"}` injecting a one-line "you have N pending shadownet messages" hint. Returns `None` on later turns — observer-only |
| `on_session_end` | End of every `run_conversation` | Drops the stashed count for the session |

Per the guide: `on_session_start`'s return is ignored; only `pre_llm_call`
can inject context into the user message (and only into the user message,
never the system prompt — this preserves the prompt cache).

## Logout / reconnect

```sh
hermes shadownet logout                  # clears the .env line + config.yaml entry
docker compose restart hermes            # or `hermes gateway restart` on bare metal

# To reconnect:
echo 'SHADOWNET_CONNECT_URL=shadow://connect?mcp=<mcp-endpoint>&token=<minted>' >> "${HERMES_HOME:-$HOME/.hermes}/.env"
hermes shadownet sync                    # re-writes mcp_servers.shadownet + re-materializes skills
hermes gateway restart
```

## Configuration reference

The plugin reads its config from environment variables (or Hermes'
`.env`). Use the single `SHADOWNET_CONNECT_URL` bootstrap, or the split
`SHADOWNET_TOKEN` + `SHADOWNET_MCP_ENDPOINT` form; the rest have defaults.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SHADOWNET_CONNECT_URL` | one of these | — | Full `shadow://connect?mcp=…&token=…` URL — a single value carrying the MCP endpoint and the bearer token. What the sidecar's connect page hands you. Inline (`token=`) form only; handoff URIs need a browser flow the plugin doesn't run. |
| `SHADOWNET_TOKEN` + `SHADOWNET_MCP_ENDPOINT` | one of these | — | Split form: bearer token plus the MCP endpoint URL, for operators wiring values via env directly. `SHADOWNET_CONNECT_URL` supersedes both when set. |
| `SHADOWNET_LONG_POLL_TIMEOUT_SECONDS` | no | `30` | Per-call timeout for the inbox long-poll. |
| `SHADOWNET_MAX_AUTO_TURNS` | no | `50` | Anti-runaway backstop: max autonomous turns per exchange before it's left for the human. The agent normally ends an exchange well before this. |
| `SHADOWNET_AUTO_IDLE_SECONDS` | no | `900` | After this much quiet, an exchange's turn budget resets. |
| `SHADOWNET_SEND_DEDUP_SECONDS` | no | `5` | Window for suppressing an exact-duplicate resend to the same contact (anti-echo). |

## How inbound works (no NAT problem)

Inbound A2A messages are delivered via the `inbox_wait` MCP tool
([RFC 0002 §4](https://github.com/shadownet-protocol/shadownet-specs)):

1. The plugin opens an MCP session against the configured endpoint (e.g.
   `<base>/u/<shadowname>/mcp`) — this is **outbound** from the user's
   machine, so no public URL or NAT traversal needed.
2. A background `asyncio.Task` calls `inbox_wait(timeout_seconds=30,
   last_event_id=…)` in a loop. The sidecar holds each call open until
   events arrive or the timeout elapses, then returns.
3. Each `inbox.message` / `task.update` event is converted to a Hermes
   `MessageEvent` and dispatched to `self.handle_message(...)` — the
   same path Telegram and other platform adapters use.

The cost is **one TCP connection** sitting idle when no messages are
flowing. Comparable to Telegram's default long-polling mode.

## Provider-agnostic

The plugin contains **no `app.sh4dow.org` strings** outside the single
`_adapter.DEFAULT_SIDECAR_BASE_URL` constant. Every install can point at
any RFC 0002-compliant sidecar by minting a
`shadow://connect?mcp=…` URL (or setting `SHADOWNET_MCP_ENDPOINT` with
`SHADOWNET_TOKEN`) against a different sidecar.

## Why the shim repo exists

Hermes' `hermes plugins install owner/repo` flow is git-clone-only — it
doesn't run pip on the cloned tree. Our adapter imports the `mcp` client
and the `shadownet` SDK transitively, so a naive `plugins install`
pointed at this monorepo would fail with `ModuleNotFoundError`. The
[`shadownet-protocol/hermes-plugin`](https://github.com/shadownet-protocol/hermes-plugin)
satellite repo is the bridge: a small shim with `plugin.yaml` +
`__init__.py` at the root that bootstraps this PyPI package on first
`register()`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Agent doesn't see `mcp_shadownet_*` tools | `hermes shadownet status` — is `mcp_servers.shadownet` written to `config.yaml`? If not, `hermes shadownet sync` then restart. |
| `/shadownet-*` commands missing from `/help` | `hermes plugins list` — is `shadownet` listed and enabled? Tail `<HERMES_HOME>/logs/agent.log` for `register()` failures. |
| `hermes shadownet doctor` reports MCP endpoint unreachable | Verify your sidecar is up and the `SHADOWNET_CONNECT_URL` token is current. |
| No inbox nudge on first turn | The platform you're chatting on may be the shadownet platform itself (auto-suppressed), or there were no pending messages at session start. |

## Updating

```sh
pip install --upgrade shadownet-hermes-plugin
hermes gateway restart
```

## License

MIT.
