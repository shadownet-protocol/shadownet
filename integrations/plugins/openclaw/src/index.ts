// Bundled-channel-entry for `@shadownet/openclaw-plugin`.
//
// One plugin install gives the user two surfaces:
//   * channel-plugin-api.js — Shadownet appears as a chat channel in
//     OpenClaw's gateway alongside Slack/Discord/iMessage.
//   * registerFull (tools-entry) — the same ten `shadownet_*` tools the
//     Phase C plugin shipped, registered as native OpenClaw tools.
//
// Pattern follows OpenClaw's bundled extensions (synology-chat, slack, etc.).

import { defineBundledChannelEntry } from "openclaw/plugin-sdk/channel-entry-contract";

import { registerShadownetTools } from "./tools-entry";

export default defineBundledChannelEntry({
  id: "shadownet",
  name: "Shadownet",
  description:
    "Identity-anchored agent-to-agent communication via the Shadownet protocol — channel + tools.",
  importMetaUrl: import.meta.url,
  plugin: {
    specifier: "./channel-plugin-api.js",
    exportName: "shadownetPlugin",
  },
  runtime: {
    specifier: "./runtime-setter-api.js",
    exportName: "setShadownetRuntime",
  },
  secrets: {
    specifier: "./secret-contract-api.js",
    exportName: "channelSecrets",
  },
  registerFull: registerShadownetTools,
});
