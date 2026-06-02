# Changelog

All notable changes to `shadownet-hermes-plugin` are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Skills are bundled into the wheel again.** They are sourced from the
  canonical `integrations/skills/` tree (no committed per-plugin copy); the
  release builds the wheel from source so they land under
  `share/hermes-plugins/shadownet/skills/` as before.
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
