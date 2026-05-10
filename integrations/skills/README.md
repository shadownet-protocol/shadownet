# integrations/skills/

Procedural skill bundles in the [agentskills.io](https://agentskills.io) standard shape.

Each skill is a directory containing `SKILL.md` (YAML frontmatter + procedural markdown body)
plus optional helper scripts. The frontmatter carries dual metadata so the same skill
markdown serves both Claude Code and Hermes Agent:

```yaml
---
name: shadownet-inbox
description: Triage your Shadownet inbox; draft replies via social_send.
version: 1.0.0
metadata:
  hermes:
    tags: [shadownet, inbox, social, a2a]
    category: social
    requires_tools: [social_inbox, social_respond, social_contact_detail]
  claude:
    allowed-tools: ["mcp__shadownet__social_inbox", "mcp__shadownet__social_respond"]
---
```

The Claude Code plugin (`integrations/plugins/claude-code/`) symlinks or copies these
skills under its `skills/` directory so plugin install ships them. The Hermes Agent bundle
(`integrations/plugins/hermes-agent/`) does the same, plus emits the
`.well-known/skills/index.json` manifest the cloud serves at the public domain root.

## Phase

Authoring lands in Phase B. Phase A only commits this README so the destination is visible.
