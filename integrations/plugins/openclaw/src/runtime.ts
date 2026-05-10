// In-process runtime singleton. The bundled-channel-entry pattern hands the
// plugin a `runtime` object once at gateway startup via setShadownetRuntime;
// from then on `getShadownetRuntime()` returns it for inbound dispatch.
//
// We type the runtime loosely (`unknown`) at the surface and narrow at the
// call sites that actually use it. The published `openclaw` npm package's
// PluginRuntime/ChannelRuntime types are nominally exposed but their
// surface is wide and likely to evolve — narrow types are an unhelpful
// stability foothold for a v0.x plugin.

let runtime: unknown;

export function setShadownetRuntime(rt: unknown): void {
  runtime = rt;
}

export function getShadownetRuntime(): unknown {
  if (!runtime) {
    throw new Error(
      "shadownet: runtime not initialised. The bundled-channel-entry contract sets it during plugin startup.",
    );
  }
  return runtime;
}

// Test-only escape hatch.
export function _resetShadownetRuntimeForTest(): void {
  runtime = undefined;
}
