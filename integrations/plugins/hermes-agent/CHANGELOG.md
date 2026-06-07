# Changelog

All notable changes to `shadownet-hermes-plugin` are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.10] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Fixed

- **Operational scaffolding no longer leaks into A2A moves.** The per-turn inject
  mixed the peer's message with operational text — a routing header, the `contextId`,
  and the literal `send_message(target="telegram:<id>")` notify target — and a weak
  model conflated the two, echoing plumbing (home-channel setup, status) to the
  contact and exposing the user's chat id. The free-form and operator injects are now
  lean (peer message + standing directives + contact notes only); the coordination
  inject keeps just the `contextId` its typed move needs. The how-to (tools, reaching
  the user) lives in the skills, and an explicit rule forbids putting instructions,
  identifiers, tool names, status, or the user's identity into a move. Keep-in-loop now
  uses the Hermes home channel (set via `/sethome`); `SHADOWNET_NOTIFY_CHAT` is no
  longer injected for routing.
- **The concierge honors involvement preferences instead of polling.** "Keep me posted
  on every message" / "stay quiet" / "always ask" now become a `shadownet_directive`
  the background honors per turn, rather than the foreground looping on
  `mcp_shadownet_inbox` to relay messages itself (which ballooned the foreground
  session to 24+ tool calls / ~30k tokens per turn). The shadownet-messaging skill
  gains `shadownet_directive` and is explicit that `shadownet_exchanges` is the only
  way to watch an exchange — `mcp_shadownet_inbox` is for stranger triage only.
- **`snet-dev.sh --clear-sessions` clears the stale gateway lock/pid.** It now also
  removes `gateway.pid` / `gateway.lock` / `gateway_state.json` / `gateway-locks` so a
  fresh boot does not run a stale-lock cleanup that races systemd's revival into a
  multi-boot storm (each transient boot opening a sidecar MCP session).

## [0.6.9] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Fixed

- **Runaway-exchange backstop that survives contextId fan-out.** Two autonomous
  Shadows playing a game saturated the model's token-per-minute limit: every
  round-trip landed on a fresh `contextId`, so the per-context `max_turns` guard
  reset to zero each round and never tripped. The engine now also enforces a
  per-contact aggregate turn budget (`SHADOWNET_MAX_CONTACT_TURNS`, default 12)
  spanning all of a contact's contexts, with the same idle reset — so a peer that
  mints a new context per message is still bounded and handed to the human.
- **`shadownet_delegate` no longer arms a perpetual exchange.** It used to persist
  the delegated instruction as a standing directive (per-contact for a new thread),
  which re-injected "play a game…" into every future turn forever. The instruction
  is now one-shot: it rides the kickoff into the opening turn and lives in the
  session history, so the exchange can actually end.

## [0.6.8] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Fixed

- **The foreground MCP block now actually fires.** 0.6.7's `pre_tool_call` hook
  keyed off `session_id`, but the block-hook call site (`agent/tool_executor.py`)
  passes only `task_id` — so the platform lookup always missed and it failed open.
  It now reads the in-flight platform ambiently from the session contextvar
  (`gateway.session_context.get_session_env("HERMES_SESSION_PLATFORM")`, propagated
  to the tool thread), so direct exchange-MCP calls are blocked in foreground
  sessions for real.

## [0.6.7] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Changed

- **No direct MCP in the foreground.** A `pre_tool_call` hook blocks the raw
  exchange-driving tools (`mcp_shadownet_send`/`respond`/`inbox_wait`/`coordinate`/
  `confirm_plan`/`accept_plan`) in user-facing sessions and steers the agent to
  `shadownet_delegate`. Background (shadownet) sessions keep them; the inbox snapshot
  stays available for stranger triage. (Hermes does not enforce a skill's
  `allowed-tools`, so gating is done in the hook.)
- **`shadownet_delegate` tool + foreground/background handoff.** The foreground hands
  a conversation to a contact's background exchange — `shadownet_delegate(contact,
  instruction)` queues a kickoff the adapter runs as a background turn; it opens (or
  continues) the thread and the background agent makes every move via the autonomous
  path and keeps the user posted. `shadownet-messaging` is now delegate-only.

## [0.6.6] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Changed

- **Keep-in-loop by default.** A background exchange now posts brief, non-blocking
  progress + outcome updates to the user by default; standing directives tune it
  from quiet (only the result) to always-ask. It informs rather than asking
  permission per turn.
- **Foreground delegates and reports instead of driving the wire.** The
  `shadownet-messaging` skill can hand a conversation to the background
  (`shadownet_directive` on the thread) and reports activity via
  `shadownet_exchanges` + the updates it already shared — it no longer pulls raw
  inbox messages for exchanges the background owns (`mcp_shadownet_inbox` is
  reserved for stranger review).

## [0.6.5] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Fixed

- **Agent status no longer leaks onto the wire.** The adapter implements
  `send_or_update_status` as a no-op, so Hermes diagnostics (rate-limit, retry,
  "nudging to continue", `(empty)`, …) are dropped instead of falling back to
  `send` and being delivered to the contact as an A2A message.

### Changed

- **`shadownet-autonomous` involvement is directive-driven.** The skill handles
  routine messages itself and involves the user per the standing instructions
  (which set the level), instead of asking for permission each turn.

## [0.6.4] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Changed

- **Backlog dispatch is throttled.** Queued turns from one poll are spaced by
  `SHADOWNET_DRAIN_DELAY_SECONDS` (default 2s) so a backlog of distinct exchanges
  doesn't fire all at once and trip the model's rate limit or the sidecar's
  connection cap. Single-message polls never wait.

## [0.6.3] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Changed

- **Inbox cursor persists across restarts.** The long-poll cursor is saved under
  `HERMES_HOME/shadownet/inbox_cursor`, so a restarted gateway resumes after the
  last handled event instead of replaying the whole inbox.
- **Per-context coalescing.** A poll that returns a backlog now responds once to
  the latest move per `contextId` rather than firing a turn per queued message —
  avoiding the startup flood (and the model rate-limit bursts it caused).
- **User-notify target resolved automatically.** The agent's `send_message` target
  is the Hermes home channel (set once via `/sethome`) when configured, else
  `SHADOWNET_NOTIFY_CHAT`. The core exchange needs neither — only proactive pings
  do. Nothing is hardcoded; the channel is read from config/env at runtime.

## [0.6.2] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Fixed

- **Free-form autonomous moves are delivered again.** A free-form turn's reply is
  now sent to the contact as its move (via `respond` on the session's `contextId`)
  instead of being dropped while the model was expected to call
  `mcp_shadownet_respond` itself — which small models often skipped, silently
  losing every move. The `shadownet-autonomous` skill now states the reply is the
  move, and the adapter routes it by per-`contextId` mode so coordination turns
  (which make typed moves via the coordinate tools) are not double-sent.

## [0.6.1] — 2026-06-07

Tracks `shadownet>=0.6.0,<0.7`.

### Changed

- **A2A exchanges run as a silent per-`contextId` autonomous loop.** Inbound
  messages from known contacts no longer surface to the operator one at a time.
  A passive `ExchangeEngine` keys a session per `contextId`; the agent makes its
  move via `mcp_shadownet_respond` and reaches the human via `send_message` only
  when a decision is needed or the exchange completes. Replaces the
  surface-every-message concierge flow.
- **Skills.** `shadownet-inbox` and `shadownet-reach-out` are replaced by
  `shadownet-messaging` and `shadownet-autonomous`, vendored in the plugin's own
  `skills/` tree. `register()` prunes skill dirs no longer in the current set, so
  a stale materialized copy can't shadow the fresh bundle.
- **Channel-bridge tools** (`shadownet_directive`, `shadownet_exchanges`,
  `shadownet_exchange_control`) let the operator steer exchanges with layered
  standing directives (global / per-contact / per-session).

### Fixed

- **Autonomous turns no longer trip the pairing gate.** The synthesized agent
  turn is marked `internal=True`, so Hermes skips user authorization for the
  (unpaired) contact's `user_id` instead of replying with an "I don't recognize
  you yet" pairing code.

## [0.6.0] — 2026-06-02

Tracks `shadownet>=0.6.0,<0.7`.

### Fixed

- **Inbound free-form messages now surface to the user.** A plain inbound
  `inbox.message` with no recognized intent and no `SHADOWNET_NOTIFY_CHAT`
  bridge was silently suppressed in `_on_event`. It now routes through the
  platform-adapter pipeline (`handle_message`) by default — opening a session
  bound to the sender and auto-loading the `shadownet-inbox` skill — so the
  user always sees it. `SHADOWNET_NOTIFY_CHAT` remains an opt-in bridge into an
  existing chat.
- **`creds_required` guidance.** The `shadownet-reach-out` skill no longer
  sends the agent down a credential-minting dead end on a `creds_required`
  rejection; it explains the contact-based remedy (the recipient adds you /
  grants messaging), with credential minting only where the network runs an
  issuer.
- **Skills are bundled into the wheel again.** They are vendored in the plugin's
  own `skills/` tree and land under `share/hermes-plugins/shadownet/skills/` in
  the wheel.
- **Profile-correct paths.** Skills and `config.yaml` are now resolved via
  `hermes_constants.get_hermes_home()` (the real `HERMES_HOME`), not a
  non-existent `HERMES_DATA_DIR`/`/opt/data` heuristic — so under Hermes
  profiles or a custom `HERMES_HOME` the skills surface in `<available_skills>`
  and the agent actually sees the `mcp_shadownet_*` tools.
- **Safe `config.yaml` writes.** The `mcp_servers.shadownet` block is written
  atomically (temp + `os.replace`) with `0600` permissions, honoring Hermes
  managed-mode and preserving `${ENV}` token templates — a crash mid-write can
  no longer truncate the user's config, and the token is no longer world-readable.
- **`shadow://` connect scheme everywhere.** The `hermes shadownet logout`
  reconnect guidance and the README install one-liners use the SDK-required
  `shadow://connect?mcp=…&token=…` form (the old `shadownet://…&base=…` was
  rejected by `parse_connect_uri`). `hermes shadownet status` redacts the token.
- **Slash commands no longer shadow the native skill commands.** The four
  skill-backed `/shadownet-*` commands (which printed raw `skill_view` JSON)
  were removed; the bundled skills provide those commands natively. Only the
  plugin-owned `/shadownet-status` and `/shadownet-logout` are registered.
- **Cross-platform + correctness fixes.** `encoding="utf-8"` on all file I/O;
  portable date formatting; `send_typing` matches the base signature;
  `stranger_review` inbound messages are surfaced; `hermes shadownet doctor`
  exits nonzero on failure; dependency upper bounds (`httpx<1`, `pydantic<3`);
  `py.typed` marker added.

### Changed

- Adapter migrated onto the stabilized v0.2 MCP surface; coordination intents
  use the unified `send`/`respond` flow and the new `propose_plan_v1` intent.

## [0.5.0] — 2026-05-30

This is the **Shadownet v0.2 release** of the Hermes plugin. Tracks
`shadownet>=0.5.0,<0.6` and the consolidated v0.2 spec set
(`shadownet-specs/feat/shadow1`). **Breaking change**; users staying on
v0.1 should pin `shadownet-hermes-plugin<0.5`.

### Added

- v0.2 MCP control surface — all tool calls go through the typed
  `shadownet.mcp.ShadownetMCPClient` async wrapper. RFC 0002 intent
  URIs (`coordinate_v1`, `confirm_plan_v1`, `accept_plan_v1`) drive
  dispatch in place of v0.1's `data_type` strings.
- New env path: `SHADOWNET_CONNECT_URL` carries the MCP endpoint and
  bearer token directly per RFC 0003 §3 (no separate
  `integration-bundle` fetch). Split form
  `SHADOWNET_TOKEN` + `SHADOWNET_MCP_ENDPOINT` also supported.
- `_hooks.py` pending-inbox check now opens a brief MCP session and
  calls the `inbox` tool — replaces the v0.1 cloud
  `/v1/account/me/social/inbox` REST endpoint that's gone in v0.2.

### Changed

- SDK pin: `shadownet>=0.4.1,<0.5` → `shadownet>=0.5.0,<0.6`. Loading
  v0.5.x of the plugin against a v0.4.x SDK will fail.
- Tool name strings drop the `social_` prefix everywhere
  (`mcp_shadownet_social_send` → `mcp_shadownet_send`, etc.) per
  RFC 0002 §4.
- Event taxonomy:
  - `inbox.message` now branches on `body.intent` rather than
    `data_type`. The receiver-side coordination dance maps cleanly
    onto the three RFC 0002 intent URIs.
  - `task.update` carries `contextId` instead of `intentId`. The
    dedup key keys on `(contextId, status)`.
- `send()` uses the typed `SendInput(to=..., body=BodySlot(text=...))`
  instead of the v0.1 `social_send(contactId, interaction, payload)`
  shape.

### Removed

- `IntegrationBundle` / `fetch_integration_bundle` — RFC 0003 has no
  bundle endpoint; the connect URI carries the MCP endpoint directly.
- `ShadownetMCPSession` — replaced by `ShadownetMCPClient` (the v0.2
  typed async wrapper around the upstream MCP streamable-HTTP client).
- `interaction` URIs — v0.1 concept replaced by `body.intent`.
- v0.1 `data_type` strings (`coordination_request`, `response`,
  `confirmation`, `confirmed`) — replaced by intent URIs.
- `SHADOWNET_SIDECAR_BASE_URL` env var — the connect URI is the
  bootstrap, not the sidecar base URL.

[0.5.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/hermes-plugin%2Fv0.5.0

## [0.4.1] — 2026-05-28

### Fixed

- `register_platform` is now tolerant of older Hermes runtimes whose
  `PlatformEntry.__init__` does not accept the v0.4.0 optional kwargs
  (`env_enablement_fn`, `platform_hint`, `allowed_users_env`,
  `allow_all_env`, …). The plugin used to fail to load entirely on
  those runtimes with
  `TypeError: PlatformEntry.__init__() got an unexpected keyword argument 'env_enablement_fn'`;
  it now warns and retries without the offending kwarg, dropping
  optional metadata one at a time until the call succeeds.

[0.4.1]: https://github.com/shadownet-protocol/shadownet/releases/tag/hermes-plugin%2Fv0.4.1

## [0.4.0] — 2026-05-28

### Added

- Six explicit slash commands registered via `ctx.register_command`:
  `/shadownet-setup`, `/shadownet-inbox`, `/shadownet-reach-out`,
  `/shadownet-coordinate`, `/shadownet-status`, `/shadownet-logout`. They
  appear in `/help`, autocomplete, and the Telegram bot menu.
- A `hermes shadownet` CLI subcommand tree via `ctx.register_cli_command`
  with `status`, `doctor`, `sync`, and `logout` subcommands.
- Three lifecycle hooks via `ctx.register_hook`: `on_session_start`
  (collects pending-inbox count), `pre_llm_call` (injects the count as
  context on the first turn), and `on_session_end` (cleans up state).
- `platform_hint` on the registered platform — short text the agent reads
  alongside the system prompt explaining what shadownet is and which
  tools / commands are available.
- `env_enablement_fn` on the registered platform — surfaces the plugin
  in `hermes gateway status` when `SHADOWNET_CONNECT_URL` is set, without
  needing an explicit `gateway.platforms.shadownet` config block.
- Logout flow: removes `mcp_servers.shadownet` from `~/.hermes/config.yaml`,
  strips `SHADOWNET_CONNECT_URL` from `~/.hermes/.env`, and sets
  `gateway.platforms.shadownet.enabled: false`. Reachable from
  `/shadownet-logout` or `hermes shadownet logout`.
- `provides_hooks`, `provides_commands`, and `provides_skills`
  declarations in `plugin.yaml` (declarative documentation per the
  Hermes plugin guide).

### Changed

- Module reorganization: helpers split out of `__init__.py` into
  `_skills.py`, `_mcp_config.py`, `_env.py`, `_hooks.py`, `_commands.py`,
  and `_cli.py`. `__init__.py` is now a slim `register(ctx)` that wires
  the surfaces together.
- The four bundled skills are registered both via `ctx.register_skill`
  (namespaced `shadownet:<name>`) and materialized into
  `~/.hermes/skills/shadownet/<name>/` so they appear in the agent's
  `<available_skills>` index — the legacy path's collision risk is
  sidestepped by the categorized layout.

### Removed

- Explicit `pyyaml>=6.0` runtime dependency. Hermes ships pyyaml, and the
  plugin's code degrades gracefully (logs a warning and skips the
  config write) when it's missing. `pyyaml` and `types-pyyaml` are kept
  in the dev dep group for standalone test environments.

[Unreleased]: https://github.com/shadownet-protocol/shadownet/compare/hermes-plugin/v0.4.0...HEAD
[0.4.0]: https://github.com/shadownet-protocol/shadownet/releases/tag/hermes-plugin%2Fv0.4.0
