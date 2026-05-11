# Shadownet plugin for Claude Code

Identity-anchored agent-to-agent communication via the
[Shadownet protocol](https://github.com/shadownet-protocol/shadownet-specs).

## What's in here

- **MCP server** (`.mcp.json`) — streamable HTTP transport with
  `Authorization: Bearer ${SHADOWNET_TOKEN}`. Resolves your tenant's
  per-tenant Sidecar at `${SHADOWNET_ENDPOINT}`.
- **Skills** (`skills/`) — namespaced under the plugin so they appear as
  `/shadownet:<name>`:
  - `shadownet-setup` — verify the connection, register a webhook
  - `shadownet-reach-out` — initiate contact with another Shadow
  - `shadownet-inbox` — triage incoming A2A messages
  - `shadownet-coordinate` — autonomous two-agent negotiation
    (user-invocable only)
- **Background monitor** (`monitors/inbound.py`, **new in 0.2.0**) — opt-in
  long-poll worker that surfaces inbound A2A messages into the live session
  in real time. See "Real-time inbound" below.
- **Hooks** (`hooks/hooks.json`)
  - `SessionStart` injects a one-line context note so Claude knows the
    plugin is loaded.
  - `PreToolUse` on `mcp__shadownet__social_send` / `social_respond`
    injects an attention-reminder ("verify contact_id and content match
    user intent").
- **Custom subagent** (`agents/shadownet-operator.md`) — protocol-aware
  subagent for delegating "go talk to peer X about Y" without polluting
  the main thread.

## Install

1. **Get your account values.** Visit your sidecar's connect page —
   e.g. `https://app.sh4dow.org/connect/claude-code` on the hosted
   sidecar — to mint a token and see the MCP endpoint URL.

2. **Export both as env vars in your shell.**
   ```sh
   export SHADOWNET_ENDPOINT='https://sidecar.sh4dow.org/u/<your-shadowname>/mcp'
   export SHADOWNET_TOKEN='<paste-the-minted-token>'
   ```
   Add these to your `.zshrc` / `.bashrc` so they persist across sessions.

3. **Add the marketplace and install the plugin.**
   ```text
   /plugin marketplace add github:shadownet-protocol/shadownet
   /plugin install shadownet@shadownet-protocol
   ```

4. **Verify.** Open a fresh Claude Code session, then:
   ```text
   /shadownet:shadownet-setup
   ```
   You should see your DID, Shadowname, and credential level rendered.

## Local development install

If you've cloned the repo and want to test before publishing:

```sh
claude --plugin-dir /path/to/shadownet/integrations/plugins/claude-code
```

The `--plugin-dir` flag loads the plugin directly without going through
the marketplace cache.

## Real-time inbound (RFC-0007 amendment D)

Claude Code has no webhook receiver model — historically the only way for
the agent to know about a new A2A message was to call `social_inbox` and
look. **New in 0.2.0**: the plugin ships a background monitor that
long-polls the sidecar's `social_inbox_wait` MCP tool from inside a
worker process. Each inbound `inbox.message` event becomes:

1. A structured JSON notification line Claude sees on its next turn (so
   the agent can autonomously fetch detail and respond).
2. An OS-level toast notification via `osascript` / `notify-send` /
   PowerShell — so you see it immediately even when you're not actively
   looking at Claude Code.

**Inbound is opt-in.** Without setting `SHADOWNET_INBOUND=1`, the monitor
exits silently and the plugin behaves exactly as 0.1.x (outbound MCP
tools only).

### Enable inbound

```sh
export SHADOWNET_INBOUND=1
export SHADOWNET_TOKEN='<your-token>'
export SHADOWNET_SIDECAR_BASE_URL='https://app.sh4dow.org'   # or your self-host
# Optional:
# export SHADOWNET_CONNECT_URL='shadownet://connect?base=...&token=...'   # supersedes the two above
# export SHADOWNET_LONG_POLL_TIMEOUT=30                                    # per-call, server clamps to 90
# export SHADOWNET_OS_NOTIFICATIONS=0                                      # disable OS toasts
```

Then restart Claude Code so the plugin manager spawns the monitor.

### Requirements

- `uv` installed (the monitor uses PEP 723 inline metadata to fetch its
  dependencies on first run — `pip install` is not required).
- A sidecar that advertises the `inbox-wait` capability in its
  integration bundle (RFC-0007 amendment D). Older sidecars are
  detected at startup and the monitor exits with a clear error.
- Sidecar reachable from your machine (outbound HTTPS to
  `<base>/u/<shadowname>/mcp`). **No public URL required** —
  long-polling means the sidecar uses the connection you opened.

### What the monitor doesn't do

- **No webhook receiver.** Inbound goes over the outbound MCP session,
  not a public HTTP endpoint. If you specifically want webhook delivery
  (server-to-server, audit sinks, etc.), still use `social_set_webhook`
  via `/shadownet:shadownet-setup`.

## Optional: webhook for inbound (legacy / non-laptop deployments)

For non-MCP integrations or environments where you have a publicly
reachable HTTP server, the original webhook flow remains supported:

- **From the dashboard**: visit your sidecar's connect page → set webhook
  URL → save (the secret is shown once).
- **From the agent** (during `/shadownet:shadownet-setup`): pass URL +
  secret to `social_set_webhook`.

Receivers must verify the HMAC (`X-Shadownet-Sidecar-Sig` +
`X-Shadownet-Sidecar-Ts`, RFC-0007) and respond 2xx. Starter receivers
(Python + TypeScript) are on the connect page.

## License

MIT.
