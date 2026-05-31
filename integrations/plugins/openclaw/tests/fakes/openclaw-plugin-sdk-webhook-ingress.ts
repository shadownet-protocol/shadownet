// Test-only stand-in for `openclaw/plugin-sdk/webhook-ingress`.
//
// The real subpath drags in the full `openclaw` peer (~500MB of transitive
// deps the plugin deliberately keeps off disk via `.npmrc`
// auto-install-peers=false). vitest.config.ts aliases the subpath here so the
// webhook unit test exercises our handler logic (signature, replay window,
// idempotency, dispatch) without the gateway runtime. The real ingress
// plumbing is covered by the Tier-1 e2e harness in deploy/compose.openclaw.yml.

import type { IncomingMessage, ServerResponse } from "node:http";

export interface PluginHttpRouteHandler {
  (req: IncomingMessage, res: ServerResponse): Promise<boolean | void> | boolean | void;
}

export interface RegisterPluginHttpRouteParams {
  path?: string | null | undefined;
  handler: PluginHttpRouteHandler;
  auth: "gateway" | "plugin";
  pluginId?: string;
  accountId?: string;
  log?: (message: string) => void;
  replaceExisting?: boolean;
  fallbackPath?: string | null;
  match?: "exact" | "prefix";
}

export function registerPluginHttpRoute(_params: RegisterPluginHttpRouteParams): () => void {
  return () => {};
}

export interface ReadRequestBodyOptions {
  maxBytes?: number;
  timeoutMs?: number;
}

export async function readRequestBodyWithLimit(
  req: IncomingMessage,
  _opts?: ReadRequestBodyOptions,
): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req as AsyncIterable<Buffer | string>) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

export interface RequestBodyLimitError extends Error {
  statusCode: number;
  code: string;
}

export function isRequestBodyLimitError(_err: unknown): _err is RequestBodyLimitError {
  return false;
}

export function requestBodyErrorToText(code: string): string {
  return code;
}

export interface WebhookInFlightLimiter {
  clear(): void;
}

export function createWebhookInFlightLimiter(): WebhookInFlightLimiter {
  return { clear() {} };
}

export interface WebhookRequestPipelineLifecycle {
  ok: boolean;
  release(): void;
}

export function beginWebhookRequestPipelineOrReject(_params: {
  req: IncomingMessage;
  res: ServerResponse;
  inFlightLimiter: WebhookInFlightLimiter;
  inFlightKey: string;
}): WebhookRequestPipelineLifecycle {
  return { ok: true, release() {} };
}