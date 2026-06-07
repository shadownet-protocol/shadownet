# integrations/skills/

Procedural skill bundles in the [agentskills.io](https://agentskills.io) standard shape:
each skill is a directory with a `SKILL.md` (YAML frontmatter + procedural markdown body)
and optional helper scripts. Frontmatter follows the Hermes Agent conventions — a `≤60`-char
one-sentence `description`, top-level `allowed-tools` naming MCP tools (Hermes uses the
single-underscore `mcp_<server>_<tool>` form; Claude Code uses `mcp__<server>__<tool>`), and a
`metadata.hermes` block.

This tree was the single source for the shadownet skills. As the host integrations diverge
(Hermes drives an autonomous per-`contextId` exchange loop the other hosts have not adopted
yet), each plugin now **vendors its own copy** of the skills it ships:

- Hermes Agent — `integrations/plugins/hermes-agent/skills/`
- Claude Code — `integrations/plugins/claude-code/` (its own skills/agents)

The shared single-source model is paused until the other hosts migrate to the same skill
surface; this directory is kept as the reference shape.
