// Lightweight setup entry. The bundled-channel-entry contract loads this
// during plugin discovery to surface CLI metadata and config primitives
// without forcing the full runtime to evaluate. v1 has no setup wizard;
// the configSchema on the channel plugin itself drives the OpenClaw UI.

export const setupEntry = {
  pluginId: "shadownet",
  hasWizard: false,
};
