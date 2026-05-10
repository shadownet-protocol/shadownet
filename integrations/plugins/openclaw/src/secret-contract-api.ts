// Plugin secrets contract. Lists the configSchema keys treated as secrets so
// OpenClaw masks them in logs, status output, and CLI dumps. v1 marks the
// MCP bearer token and the webhook signing secret as sensitive; the endpoint
// URL is non-sensitive (it appears in published artifacts and on the connect
// page).

export const channelSecrets = {
  channelKey: "shadownet",
  fields: ["token", "secret"] as const,
};
