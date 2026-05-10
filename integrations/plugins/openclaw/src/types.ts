// Shared types for the Shadownet OpenClaw plugin.
//
// The Phase C tool surface (`ShadownetConfig`, `ShadownetTool`, `ShadownetToolResult`,
// `JsonRpcResponse`) lives alongside the new Phase D channel types
// (`ResolvedShadownetAccount`, `ShadownetInboundMessage`, `ShadownetEvent`).
// The two surfaces share `ShadownetClient` (src/client.ts) for outbound calls.

// ----- Phase C (tool surface) -----

export interface ShadownetConfig {
  endpoint: string;
  token: string;
}

export interface ShadownetTool {
  name: string;
  description: string;
  parameters: unknown;
  execute(id: string, params: Record<string, unknown>): Promise<ShadownetToolResult>;
}

export interface ShadownetToolResult {
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
}

export interface JsonRpcResponse<T = unknown> {
  jsonrpc?: "2.0";
  id?: number;
  result?: T;
  error?: { code: number; message: string; data?: unknown };
}

// ----- Phase D (channel surface) -----

export type ShadownetDmPolicy = "allowlist" | "open";

export interface ResolvedShadownetAccount {
  accountId: string;
  enabled: boolean;
  endpoint: string;
  token: string;
  secret: string;
  webhookPath: string;
  dmPolicy: ShadownetDmPolicy;
  allowedShadownames: readonly string[];
  rateLimitPerMinute: number;
}

// RFC-0007 §Inbound notifications event types. Receivers MUST ignore
// unrecognised event types — but at parse time we narrow to the four we
// understand and route the rest through `unknownEvents`.
export type ShadownetEventType =
  | "inbox.message"
  | "task.update"
  | "freshness.expired"
  | "presentation.failed"
  | "test.ping";

export interface ShadownetEventEnvelope {
  "shadownet:v": "0.1";
  event: ShadownetEventType | (string & {});
  occurredAt: number;
  data: Record<string, unknown>;
}

export interface ShadownetInboundMessage {
  accountId: string;
  intentId: string;
  contactId: string;
  messageId: string;
  interaction?: string | undefined;
  // Resolved by social_inbox after the webhook fires; the bare envelope only
  // carries metadata.
  body: string;
  senderShadowname?: string | undefined;
  receivedAt: number;
}
