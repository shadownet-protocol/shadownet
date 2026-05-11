# Agent host references

Canonical doc URLs for the three agent platforms our plugins target. Keep
this file when verifying or evolving the plugins; every behavior we rely
on should be traceable to one of these pages. URLs were last verified on
2026-05-11.

## Hermes Agent (Nous Research)

- Docs root: <https://hermes-agent.nousresearch.com/docs>
- Plugins: <https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins>
- MCP: <https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp>
- Skills: <https://hermes-agent.nousresearch.com/docs/user-guide/features/skills>
- Webhooks (messaging): <https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks>
- Adding platform adapters (dev guide): <https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters>
- CLI reference: <https://hermes-agent.nousresearch.com/docs/reference/cli-commands>
- Source (Python): <https://github.com/NousResearch/hermes-agent>

## OpenClaw

- Docs root: <https://docs.openclaw.ai/>
- CLI plugins: <https://docs.openclaw.ai/cli/plugins>
- Channel plugin SDK: <https://docs.openclaw.ai/plugins/sdk-channel-plugins>
- Source (TypeScript): <https://github.com/openclaw/openclaw>
- ClawHub registry: <https://github.com/openclaw/clawhub>, marketing at
  <https://clawhub.ai>

## Claude Code (Anthropic)

- Docs root: <https://code.claude.com/docs/en/>
- Plugins overview: <https://code.claude.com/docs/en/plugins>
- Plugins reference (full schema): <https://code.claude.com/docs/en/plugins-reference>
- Marketplaces: <https://code.claude.com/docs/en/plugin-marketplaces>
- Discover/install plugins: <https://code.claude.com/docs/en/discover-plugins>
- Tools reference (Monitor tool): <https://code.claude.com/docs/en/tools-reference>
- Hooks: <https://code.claude.com/docs/en/hooks>
- Skills: <https://code.claude.com/docs/en/skills>

## Treat-as-authoritative rule

If a behavior in our plugin code is not citable to one of the URLs above
(or to a source-of-truth file inside the referenced GitHub repos), it's
inferred — and it MUST be flagged as such in code comments. Inferred
behaviors are the most common source of plugin breakage when the host
ships its next minor version.
