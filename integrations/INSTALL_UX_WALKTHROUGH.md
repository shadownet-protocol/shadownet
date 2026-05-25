# Install UX walkthrough

How easy is it for a user to get Shadownet running on each of the three
agent platforms we support? Two scenarios per platform: a **hosted
Sidecar** (the user signs up on someone else's sh4dow.org-style cloud)
and a **self-hosted Sidecar** (the user runs `shadownet-local` or an
RFC-0007-compliant Sidecar on their own infra).

For each path we name the canonical doc URL we verified against (full
list in [`AGENT_HOST_REFERENCE.md`](./AGENT_HOST_REFERENCE.md)).

## TL;DR

| Host | Hosted Sidecar | Self-hosted Sidecar | Floor / ceiling |
| --- | --- | --- | --- |
| **Claude Code** | 1 prompt: paste `shadownet://connect?...` URL. Optional second prompt for the inbound-monitor toggle. | Same — connect URL embeds the self-host's base. | One-paste install via the MCP stdio↔HTTP proxy (`bin/mcp-shadownet-proxy.py`). Token never leaves the keychain. |
| **Hermes Agent** | Paste a three-line block (`pip install` + `~/.hermes/.env` write + `gateway restart`) from the sidecar's `/connect/hermes-agent` page — token embedded in the `SHADOWNET_CONNECT_URL` value. | Same — the connect URL the self-host serves embeds its own base. | Telegram-tier ergonomics inside Hermes (long-poll, no NAT). |
| **OpenClaw** | `openclaw plugins install` + configSchema prompts (endpoint, token). User must expose the gateway HTTP port for inbound webhooks. | Same, plus the user-side reachability requirement still applies. | Requires public reachability for inbound. Outbound tools work without. |

---

## Claude Code

**Authoritative docs:** [plugins reference](https://code.claude.com/docs/en/plugins-reference) · [marketplaces](https://code.claude.com/docs/en/plugin-marketplaces) · [discover plugins](https://code.claude.com/docs/en/discover-plugins)

### Install command (identical for hosted & self-hosted)

```
/plugin marketplace add github:shadownet-protocol/shadownet
/plugin install shadownet@shadownet-protocol
```

After install, the plugin's `userConfig` block triggers **one or two
prompts** (handled natively by Claude Code per the docs' "User
configuration" section):

| Prompt | Stored where | Sensitive |
| --- | --- | --- |
| **`shadownet://connect?...` URL** | System keychain (single value carries base + token) | **yes** (`sensitive: true`) |
| **Enable real-time inbound monitor?** | settings.json (boolean) | no — defaults to false; user can skip |

The connect URL is exactly what RFC-0008's `<base>/connect/claude-code`
endpoint serves. Workflow for **hosted** Sidecars:

1. Visit `<your-sidecar>/connect/claude-code` (e.g.
   `https://app.sh4dow.org/connect/claude-code`).
2. Copy the `shadownet://connect?base=...&token=...` URL.
3. Paste it into Claude Code's one prompt.

### How the proxy makes this work

`.mcp.json` declares an stdio MCP server pointing at
`${CLAUDE_PLUGIN_ROOT}/bin/mcp-shadownet-proxy.py` with the connect URL
exported as `SHADOWNET_CONNECT_URL`. At session start the proxy:

1. Parses the URL via the shared `shadownet.connect.url` parser.
2. Fetches the integration bundle to discover the per-tenant MCP endpoint
   (one HTTP call, ~50ms).
3. Opens an HTTP+SSE MCP session against that endpoint with the parsed
   token as Bearer.
4. Bridges every JSON-RPC message between Claude Code (stdio) and the
   sidecar (HTTP+SSE) until either side closes.

Cost: one extra process spawn + one bundle fetch per Claude Code session
start. Latency is dominated by the upstream MCP server; the proxy itself
is just a pipe.

For **self-hosted** Sidecars: the same `<base>/connect/claude-code` page
exists (RFC-0008 makes it mandatory once the Sidecar advertises the
`bundle` capability); operators paste their self-host's base. No
difference in the install flow.

After the prompts, `${user_config.endpoint}` and `${user_config.token}`
are substituted into `.mcp.json` automatically. No shell env vars to
export, no `.zshrc` edits.

### Real-time inbound

The plugin ships a background monitor (`monitors/inbound.py`) declared
under `experimental.monitors` in `plugin.json`. Per the
[plugins-reference docs](https://code.claude.com/docs/en/plugins-reference#monitors)
this runs as a persistent subprocess for the session lifetime; each
stdout line becomes a notification Claude sees on its next turn. The
monitor also fires an OS-level toast (`osascript` / `notify-send` /
PowerShell) so the user sees the message even when not actively in
Claude Code.

**Opt-in.** If the user leaves `inbound_enabled` false (default), the
monitor exits silently at startup. No background process.

### What the user does NOT have to do

- Edit any YAML or JSON config by hand.
- Export shell environment variables.
- Configure a public webhook URL (long-poll path means no inbound HTTP
  server on the user's machine).
- Restart anything beyond the plugin enable flow.

### Outstanding caveats

- Claude Code monitors are an **experimental component** per the spec
  (manifest schema may change). Documented at v2.1.105+. The
  `experimental.monitors` block we ship is the post-deprecation
  location.
- The monitor's user-visibility (whether stdout lines render in the
  chat scrollback vs. only the agent's context) is undocumented; we
  mitigate with the OS-toast fallback.

---

## Hermes Agent

**Authoritative docs:** [plugins guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins) · [CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands)

### Install command (identical for hosted & self-hosted)

Visit `<base>/connect/hermes-agent` on the sidecar. The page mints a
per-user token and hands the user a paste-ready block; inside the
Hermes environment they paste:

```sh
pip install shadownet-hermes-plugin
echo 'SHADOWNET_CONNECT_URL=shadownet://connect?base=<sidecar>&token=<minted>' >> ~/.hermes/.env
hermes gateway restart
```

Hermes reads `~/.hermes/.env` at startup (`python-dotenv` is in
Hermes' core deps), so no shell `export` is needed — the values
survive container restarts and are picked up on the next gateway
launch.

| Variable | Required | Default |
| --- | --- | --- |
| `SHADOWNET_CONNECT_URL` | one of these | — (supersedes the two below when set) |
| `SHADOWNET_TOKEN` | one of these | — |
| `SHADOWNET_SIDECAR_BASE_URL` | no | `https://app.sh4dow.org` |
| `SHADOWNET_LONG_POLL_TIMEOUT_SECONDS` | no | `30` |

**Hosted Sidecar** flow:
1. Visit `<hosted-sidecar>/connect/hermes-agent`.
2. Copy the three-line install block (the sidecar embeds the token
   into the `SHADOWNET_CONNECT_URL` it serves).
3. Paste inside the Hermes environment; gateway restart.

**Self-hosted Sidecar** flow:
1. Visit `<your-sidecar>/connect/hermes-agent` on your self-host.
2. Same paste-and-restart — the `SHADOWNET_CONNECT_URL` the page
   serves embeds your self-host's base URL, so there's nothing extra
   to override.

After restart, `register(ctx)` runs at Hermes startup and:
- Registers the four skills (`shadownet-setup`, `shadownet-reach-out`,
  `shadownet-inbox`, `shadownet-coordinate`) via `ctx.register_skill`.
- Registers the `shadownet` platform adapter via `ctx.register_platform`.

The adapter opens an MCP `ClientSession` to the Sidecar's
`<base>/u/<shadowname>/mcp`, then runs `inbox_loop` long-polling for
A2A messages. Each `inbox.message` event becomes a
`handle_message(MessageEvent(...))` call into Hermes's gateway — same
path the Telegram and Discord adapters use, per the
[platform adapter dev guide](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters).

### Why pip, not `hermes plugins install`

The `hermes plugins install owner/repo` Git-clone path doesn't resolve
Python dependencies for the cloned tree (confirmed against upstream
source: `hermes_cli/plugins_cmd.py:_install_plugin_core` just
`git clone --depth 1`'s the repo and reads `<root>/plugin.yaml` +
`<root>/__init__.py` — no pip step). Our adapter imports
`mcp.client.session` and the `shadownet` SDK transitively, so the
Git-clone install would proceed but `register()` would fail at
startup with `ModuleNotFoundError`. The upstream Hermes plugin guide
directs plugins with third-party Python deps to distribute via pip;
that's the canonical path for our shape.

The Git-clone path is also incompatible with our monorepo layout —
Hermes looks for `plugin.yaml` at the cloned repo root, and we have
three plugins coexisting under `integrations/plugins/`. The
dashboard-served paste block hides this constraint from the user
entirely.

### What the user does NOT have to do

- Hand-edit `~/.hermes/config.yaml`.
- Set shell environment variables manually (the paste block writes to
  `~/.hermes/.env` so values persist across restarts).
- Run `hermes mcp add` (the plugin's adapter owns its own MCP session).
- Run `hermes webhook subscribe` (the long-poll path is in-MCP, no
  public URL needed).
- Configure any reverse proxy / tunnel for inbound.

### Trade-off: no install-time `requires_env` wizard

Hermes' wizard that prompts for `requires_env` values fires only on
the Git-clone install path (verified in
`hermes_cli/plugins.py:prompt_for_requires_env`, called from
`_install_plugin_core` and nowhere else). On the pip entry-point path
Hermes never reads `plugin.yaml`. We compensate two ways:

1. The sidecar's `/connect/hermes-agent` page pre-fills the
   `SHADOWNET_CONNECT_URL` value in the paste block, so the user never
   types or sees the token directly.
2. The plugin's `_adapter._resolve_config` raises a clear
   `RuntimeError` at startup if the token is missing, pointing the
   user back to `<base>/connect/hermes-agent`.

---

## OpenClaw

**Authoritative docs:** [CLI plugins](https://docs.openclaw.ai/cli/plugins) · [channel plugin SDK](https://docs.openclaw.ai/plugins/sdk-channel-plugins)

### Install command

```sh
openclaw plugins install clawhub:shadownet
# or for development:
openclaw plugins install /path/to/integrations/plugins/openclaw
```

The CLI consults the plugin's `openclaw.plugin.json` `configSchema` and
prompts for:

| Field | Required | Sensitive |
| --- | --- | --- |
| `endpoint` (per-tenant MCP URL) | yes | no |
| `token` (bearer) | yes | yes (`writeOnly: true`) |

OpenClaw's channel-plugin model (`gateway.startAccount` per-account
worker) is conceptually similar to Hermes's platform adapter but
operates over its **own HTTP webhook receiver**. The plugin runtime
registers a route via `registerPluginHttpRoute({ auth: 'plugin' })`
that the Sidecar pushes events to.

**Hosted Sidecar** flow:
1. Visit `<hosted-sidecar>/connect/openclaw` for the snippet.
2. Run `openclaw plugins install clawhub:shadownet`; paste endpoint + token.
3. **Make the OpenClaw Gateway HTTP port publicly reachable** (the user
   needs this — OpenClaw does not ship a tunnel).
4. Inside the agent, call `/shadownet:shadownet-setup`.

**Self-hosted Sidecar**: identical flow, just point at your own host.

### The hard requirement: reachable HTTP

The OpenClaw plugin uses webhook inbound (not the long-poll MCP-tool
path the Hermes/Claude-Code plugins use), because OpenClaw is primarily
an MCP server — its MCP-client surface is a registry of saved server
definitions, not a long-lived session a plugin can ride. So inbound
arrives via the Sidecar pushing to the user's Gateway port.

For desktop OpenClaw installs behind NAT, the user needs cloudflared,
ngrok, or similar. We document this in the plugin's README but do not
ship a tunnel. RFC-0008's normative spec leaves this requirement to the
host's documentation.

### What the user does NOT have to do

- Hand-edit anything beyond entering the configSchema prompts. OpenClaw
  writes the result to its plugin config automatically.

### What the user DOES still have to do (regression from the other two)

- Make a public URL reachable from the Sidecar.
- Run the in-agent `/shadownet:shadownet-setup` once.

### Inferred behaviors (OpenClaw docs gaps)

- The CLI's exact prompt UX for `configSchema` is not documented. We
  match a standard JSON Schema layout.
- The full secret field (`secret`, used for HMAC verification of inbound
  webhook deliveries) is in the runtime config but NOT prompted at
  install. The user has to either edit the gateway config after install
  OR have the Sidecar's webhook registration step return the secret and
  the plugin store it. Either way, this is a known wrinkle, not a
  blocker for v1.

---

## Cross-host scoring

| Dimension | Claude Code | Hermes | OpenClaw |
| --- | --- | --- | --- |
| Number of user prompts at install | 3 (native UI) | 0 (dashboard-served paste block) | 2 |
| Manual YAML/JSON editing? | None | None | None |
| Public URL required? | No | No | **Yes** |
| Secret storage | Keychain | `~/.hermes/.env` | gateway config file |
| Inbound transport | long-poll MCP tool (in-band) | long-poll MCP tool (in-band) | webhook (out-of-band) |
| Native install command | `/plugin install` | `pip install` (entry-point) | `openclaw plugins install` |
| Spec-supported one-paste connect-URL? | Not yet wired (`userConfig` doesn't accept `shadownet://`) | Yes via `SHADOWNET_CONNECT_URL` | Not wired |
| Provider-agnostic? | Yes | Yes (`SHADOWNET_SIDECAR_BASE_URL`) | Yes (configSchema is plain endpoint) |

## What we'd build next to close gaps

These are NOT bugs — they're places the UX could be tightened further:

1. **Claude Code: accept the `shadownet://` connect URL** as a single
   `userConfig` field (one paste replaces two prompts). The plugin's
   `userConfig` doesn't have a "connect URL" type natively; we'd add a
   one-time helper command that parses the URL and writes both
   `endpoint` and `token` via `claude plugin config`.
2. **OpenClaw: connect URL parser** (already in
   `src/connect/url.ts`) wired into the channel plugin's setup phase so
   the user pastes one URL instead of two values.
3. **OpenClaw: stash the webhook secret in configSchema** so users
   don't have to edit the gateway config file after install.

None of these block the v0.1 ship; they're the "even smoother" follow-ups
once the spec is at Last Call and we have real install telemetry.
