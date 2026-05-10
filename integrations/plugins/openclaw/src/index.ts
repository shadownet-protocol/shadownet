import { ShadownetClient } from "./client";
import { tools } from "./tools";
import type { ShadownetConfig, ShadownetTool } from "./types";

// Local SDK type shim. The OpenClaw runtime instantiates this `api` object
// before calling our `register` — we only need types narrow enough for our
// own compile-time correctness. Adding a hard dependency on an external
// `openclaw` npm package isn't required: OpenClaw evaluates `dist/index.js`
// and reads the default export's `id` / `register` directly.
//
// If the SDK ever pins a TypeScript declarations package on a public
// registry, we'll switch to importing the real types here. Until then this
// shim mirrors the `api.registerTool` and `api.pluginConfig` surface
// referenced in https://docs.openclaw.ai/plugins/sdk-entrypoints.

export interface OpenClawPluginApi {
  pluginConfig: Record<string, unknown>;
  registerTool(tool: ShadownetTool): void;
}

export interface PluginEntry {
  id: string;
  name: string;
  description: string;
  register(api: OpenClawPluginApi): void;
}

function definePluginEntry<T extends PluginEntry>(spec: T): T {
  return spec;
}

export default definePluginEntry({
  id: "shadownet",
  name: "Shadownet",
  description:
    "Identity-anchored agent-to-agent communication via the Shadownet protocol.",
  register(api: OpenClawPluginApi) {
    const cfg = api.pluginConfig as Partial<ShadownetConfig> | undefined;
    if (!cfg?.endpoint || !cfg?.token) {
      throw new Error(
        "shadownet: missing endpoint or token in plugin config. " +
          "Configure via OpenClaw's plugin UI — values from https://app.sh4dow.org/connect.",
      );
    }
    const client = new ShadownetClient(cfg.endpoint, cfg.token);
    for (const tool of tools(client)) {
      api.registerTool(tool);
    }
  },
});
