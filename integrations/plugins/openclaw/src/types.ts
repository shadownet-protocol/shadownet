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

// RFC 0002 §7 inbound notification event types. Receivers MUST ignore
// unrecognised event types — at parse time we narrow to the two v0.2
// events and route the rest through the "ignore unrecognised" path.
export type ShadownetEventType = "inbox.message" | "task.update";

export interface ShadownetEventEnvelope {
  "shadownet:v": "0.2";
  // RFC 0002 §7 Path 2: webhook payloads carry a top-level event_id that
  // receivers MUST use for idempotency. Byte-identical to the eventId the
  // same event would carry via inbox_wait or notifications/shadownet/* —
  // cross-transport dedupe relies on it.
  event_id: string;
  event: ShadownetEventType | (string & {});
  occurredAt: number;
  data: Record<string, unknown>;
}

export interface ShadownetInboundMessage {
  accountId: string;
  // A2A contextId threads the conversation (RFC 0001 §8.2); replaces v0.1
  // intentId.
  contextId: string;
  // Sender identifier — a Shadowname or a shadow:// URI (RFC 0001 §3.3);
  // replaces v0.1 contactId.
  from: string;
  messageId: string;
  // The envelope's body.intent URI when present (RFC 0001 §8.5); replaces
  // v0.1 interaction.
  intent?: string | undefined;
  // Resolved by the inbox tool after the webhook fires; the bare event only
  // carries correlation metadata.
  body: string;
  receivedAt: number;
}
