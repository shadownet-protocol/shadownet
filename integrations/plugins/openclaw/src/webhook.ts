// Inbound webhook handler for the Shadownet channel plugin.
//
// Validates RFC-0007 signatures (`X-Webhook-Signature` — the compatibility
// header shadownet-cloud emits alongside `X-Shadownet-Sidecar-Sig`), enforces
// the 5-minute replay window via `X-Shadownet-Sidecar-Ts`, and dispatches the
// resolved inbound message into OpenClaw's turn pipeline via the messaging
// module.

import { createHmac, timingSafeEqual } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

import {
  beginWebhookRequestPipelineOrReject,
  createWebhookInFlightLimiter,
  isRequestBodyLimitError,
  readRequestBodyWithLimit,
  registerPluginHttpRoute,
  requestBodyErrorToText,
} from "openclaw/plugin-sdk/webhook-ingress";

import { ShadownetClient } from "./client";
import { dispatchShadownetInboundTurn } from "./messaging";
import { ShadownetRateLimiter } from "./security";
import type {
  ResolvedShadownetAccount,
  ShadownetEventEnvelope,
  ShadownetInboundMessage,
} from "./types";

const CHANNEL_ID = "shadownet";
const KNOWN_EVENTS = new Set([
  "inbox.message",
  "task.update",
  "freshness.expired",
  "presentation.failed",
  "test.ping",
]);
const REPLAY_WINDOW_SECONDS = 5 * 60;
const PREAUTH_MAX_BODY_BYTES = 64 * 1024;
const PREAUTH_BODY_TIMEOUT_MS = 5_000;
const IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;
const IDEMPOTENCY_MAX_ENTRIES = 10_000;

const inFlightLimiter = createWebhookInFlightLimiter();
const rateLimiters = new Map<string, ShadownetRateLimiter>();
const idempotencyLru = new Map<string, number>(); // messageId -> firstSeenAtMs

function getRateLimiter(account: ResolvedShadownetAccount): ShadownetRateLimiter {
  let rl = rateLimiters.get(account.accountId);
  if (!rl) {
    rl = new ShadownetRateLimiter(account.rateLimitPerMinute);
    rateLimiters.set(account.accountId, rl);
  }
  return rl;
}

export function _resetWebhookStateForTest(): void {
  rateLimiters.clear();
  idempotencyLru.clear();
  inFlightLimiter.clear();
}

function recordSeen(messageId: string, now = Date.now()): boolean {
  // Returns true if this is a fresh delivery, false if it has been seen
  // within the TTL.
  const seen = idempotencyLru.get(messageId);
  if (seen !== undefined && now - seen < IDEMPOTENCY_TTL_MS) {
    return false;
  }
  // Move-to-end semantics: delete + reinsert keeps the freshest entries.
  idempotencyLru.delete(messageId);
  idempotencyLru.set(messageId, now);
  while (idempotencyLru.size > IDEMPOTENCY_MAX_ENTRIES) {
    const oldest = idempotencyLru.keys().next().value;
    if (!oldest) break;
    idempotencyLru.delete(oldest);
  }
  return true;
}

function readSingleHeader(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

function constantTimeEquals(a: string, b: string): boolean {
  // crypto.timingSafeEqual requires equal-length buffers.
  if (a.length !== b.length) return false;
  return timingSafeEqual(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

function verifyHmac(body: Buffer, secret: string, providedHex: string): boolean {
  const expected = createHmac("sha256", secret).update(body).digest("hex");
  return constantTimeEquals(expected, providedHex);
}

function respond(res: ServerResponse, status: number, body?: string): void {
  res.statusCode = status;
  if (body !== undefined) {
    res.setHeader("Content-Type", "application/json");
    res.end(body);
  } else {
    res.end();
  }
}

export interface HandlerDeps {
  account: ResolvedShadownetAccount;
  // Async deliver — typically `dispatchShadownetInboundTurn` from messaging.ts.
  // Tests inject a stub. Returns the resolved inbound message (or null if the
  // event was non-actionable, e.g. test.ping) for inspection.
  deliver?:
    | ((params: {
        account: ResolvedShadownetAccount;
        msg: ShadownetInboundMessage;
      }) => Promise<void>)
    | undefined;
  log?:
    | {
        info?: (msg: string) => void;
        warn?: (msg: string) => void;
        error?: (msg: string) => void;
      }
    | undefined;
  // Test seam — overrideable clock + body limits.
  now?: (() => number) | undefined;
}

export function createWebhookHandler(
  deps: HandlerDeps,
): (req: IncomingMessage, res: ServerResponse) => Promise<void> {
  const { account, log, deliver = dispatchShadownetInboundTurn } = deps;
  const rateLimiter = getRateLimiter(account);
  const now = deps.now ?? (() => Date.now());

  return async (req, res) => {
    if (req.method !== "POST") {
      respond(res, 405, JSON.stringify({ error: "method_not_allowed" }));
      return;
    }

    const lifecycle = beginWebhookRequestPipelineOrReject({
      req,
      res,
      inFlightLimiter,
      inFlightKey: account.accountId,
    });
    if (!lifecycle.ok) return;

    try {
      let body: string;
      try {
        body = await readRequestBodyWithLimit(req, {
          maxBytes: PREAUTH_MAX_BODY_BYTES,
          timeoutMs: PREAUTH_BODY_TIMEOUT_MS,
        });
      } catch (err) {
        if (isRequestBodyLimitError(err)) {
          respond(res, err.statusCode, JSON.stringify({ error: requestBodyErrorToText(err.code) }));
        } else {
          respond(res, 400, JSON.stringify({ error: "invalid_body" }));
        }
        return;
      }
      const bodyBuf = Buffer.from(body, "utf8");

      const signature = readSingleHeader(req.headers["x-webhook-signature"]);
      if (!signature) {
        respond(res, 401, JSON.stringify({ error: "missing_signature" }));
        return;
      }
      if (!verifyHmac(bodyBuf, account.secret, signature)) {
        log?.warn?.(`shadownet: invalid HMAC from ${req.socket?.remoteAddress ?? "unknown"}`);
        respond(res, 401, JSON.stringify({ error: "invalid_signature" }));
        return;
      }

      const tsHeader = readSingleHeader(req.headers["x-shadownet-sidecar-ts"]);
      const tsSeconds = tsHeader ? Number.parseInt(tsHeader, 10) : NaN;
      if (!Number.isFinite(tsSeconds)) {
        respond(res, 401, JSON.stringify({ error: "missing_or_invalid_timestamp" }));
        return;
      }
      const skew = Math.abs(Math.floor(now() / 1000) - tsSeconds);
      if (skew > REPLAY_WINDOW_SECONDS) {
        log?.warn?.(`shadownet: replay window exceeded — skew=${skew}s`);
        respond(res, 401, JSON.stringify({ error: "timestamp_skew" }));
        return;
      }

      let envelope: ShadownetEventEnvelope;
      try {
        envelope = JSON.parse(body) as ShadownetEventEnvelope;
      } catch {
        respond(res, 400, JSON.stringify({ error: "invalid_json" }));
        return;
      }
      if (envelope?.["shadownet:v"] !== "0.1" || typeof envelope.event !== "string") {
        respond(res, 400, JSON.stringify({ error: "unrecognised_envelope" }));
        return;
      }

      // RFC-0007: receivers MUST ignore unrecognised events. We ACK 200 but
      // skip dispatch.
      if (!KNOWN_EVENTS.has(envelope.event)) {
        log?.info?.(`shadownet: ignoring unrecognised event ${envelope.event}`);
        respond(res, 200, JSON.stringify({ ok: true, ignored: true }));
        return;
      }

      // Per-account rate limit (post-auth). We key on the IP rather than the
      // sender shadowname to defend against burst from a single source.
      const rlKey = req.socket?.remoteAddress ?? "unknown";
      if (!rateLimiter.check(rlKey)) {
        respond(res, 429, JSON.stringify({ error: "rate_limited" }));
        return;
      }

      // Idempotency on data.messageId — only inbox.message carries one.
      const messageId =
        typeof envelope.data?.messageId === "string"
          ? (envelope.data.messageId as string)
          : `${envelope.event}-${envelope.occurredAt}`;
      if (!recordSeen(messageId, now())) {
        respond(res, 200, JSON.stringify({ ok: true, idempotent: true }));
        return;
      }

      // ACK before dispatching — RFC-0007 receivers respond 2xx and process
      // asynchronously.
      respond(res, 200, JSON.stringify({ ok: true }));

      // For non-`inbox.message` events (task.update, freshness.expired,
      // presentation.failed, test.ping) we just log and skip turn dispatch
      // for v1.
      if (envelope.event !== "inbox.message") {
        log?.info?.(`shadownet: received ${envelope.event} (no turn dispatch in v1)`);
        return;
      }

      const data = envelope.data as Record<string, unknown>;
      const intentId = typeof data.intentId === "string" ? data.intentId : "";
      const contactId = typeof data.contactId === "string" ? data.contactId : "";
      if (!intentId || !contactId) {
        log?.warn?.("shadownet: inbox.message missing intentId or contactId");
        return;
      }

      const client = new ShadownetClient(account.endpoint, account.token);
      let inbound: ShadownetInboundMessage;
      try {
        const inboxResult = (await client.call("social_inbox", {
          contact_id: contactId,
          limit: 1,
        })) as { items?: Array<Record<string, unknown>> } | null;
        const item = inboxResult?.items?.[0];
        if (!item) {
          log?.warn?.(`shadownet: no inbox item for intent ${intentId}`);
          return;
        }
        const payload = (item.payload ?? {}) as Record<string, unknown>;
        const text =
          typeof payload.text === "string"
            ? payload.text
            : JSON.stringify(payload);
        const interaction =
          typeof item.interaction === "string" ? item.interaction : undefined;
        inbound = {
          accountId: account.accountId,
          intentId,
          contactId,
          messageId,
          ...(interaction !== undefined ? { interaction } : {}),
          body: text,
          receivedAt:
            typeof item.receivedAt === "number" ? item.receivedAt : Math.floor(now() / 1000),
        };
      } catch (err) {
        log?.error?.(
          `shadownet: failed to fetch inbox body for ${intentId}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
        return;
      }

      try {
        await deliver({ account, msg: inbound });
      } catch (err) {
        log?.error?.(
          `shadownet: dispatch failed for ${intentId}: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    } finally {
      lifecycle.release();
    }
  };
}

export interface RegisterRouteParams {
  account: ResolvedShadownetAccount;
  log?: HandlerDeps["log"];
  deliver?: HandlerDeps["deliver"];
}

export function registerShadownetWebhookRoute(params: RegisterRouteParams): () => void {
  const handler = createWebhookHandler({
    account: params.account,
    ...(params.log !== undefined ? { log: params.log } : {}),
    ...(params.deliver !== undefined ? { deliver: params.deliver } : {}),
  });
  return registerPluginHttpRoute({
    path: params.account.webhookPath,
    auth: "plugin",
    pluginId: CHANNEL_ID,
    accountId: params.account.accountId,
    log: (msg) => params.log?.info?.(msg),
    handler,
  });
}
