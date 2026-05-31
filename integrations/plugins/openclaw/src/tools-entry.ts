// `registerFull` callback for the bundled-channel-entry contract — installs
// the Shadownet v0.2 MCP tools (RFC 0002 §4) as native OpenClaw tools.
//
// Reads `endpoint` + `token` from `api.pluginConfig` (the same channel
// configSchema the channel plugin uses), constructs a ShadownetClient, and
// calls `api.registerTool` for each entry. Tool registration is independent
// of the channel: a user can install this plugin and rely on the tools
// alone, or rely on both surfaces simultaneously.

import { ShadownetClient } from "./client";
import { tools } from "./tools/tools";
import type { ShadownetConfig } from "./types";

interface MinimalApi {
  pluginConfig?: Record<string, unknown> | undefined;
  registerTool: (tool: unknown, opts?: unknown) => void;
}

export function registerShadownetTools(api: unknown): void {
  // The OpenClawPluginApi surface from the published types includes optional
  // fields we don't use; cast to a narrower local shape rather than depending
  // on the full SDK type.
  const narrowed = api as MinimalApi;
  const cfg = narrowed.pluginConfig as Partial<ShadownetConfig> | undefined;
  if (!cfg?.endpoint || !cfg?.token) {
    // Channel-only installs are valid (the channel handler builds its own
    // client); skip tool registration silently when tool-mode credentials
    // are absent. Logging happens via OpenClaw's plugin status surface.
    return;
  }
  const client = new ShadownetClient(cfg.endpoint, cfg.token);
  for (const tool of tools(client)) {
    narrowed.registerTool(tool);
  }
}
