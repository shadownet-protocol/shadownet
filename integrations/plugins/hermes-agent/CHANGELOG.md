# Changelog

All notable changes to `shadownet-hermes-plugin` are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
