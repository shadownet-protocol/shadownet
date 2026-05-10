import { createHmac } from "node:crypto";
import { IncomingMessage, ServerResponse } from "node:http";
import { Socket } from "node:net";
import { Readable } from "node:stream";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetWebhookStateForTest, createWebhookHandler } from "../src/webhook";
import type { ResolvedShadownetAccount, ShadownetInboundMessage } from "../src/types";

const ACCOUNT: ResolvedShadownetAccount = {
  accountId: "default",
  enabled: true,
  endpoint: "https://example.test/u/alice/mcp",
  token: "tk-abcdef0123456789",
  secret: "0123456789abcdef0123456789abcdef",
  webhookPath: "/shadownet/inbox",
  dmPolicy: "allowlist",
  allowedShadownames: [],
  rateLimitPerMinute: 60,
};

const NOW_S = 1759200200;

interface FakeRequest {
  body: Buffer;
  headers: Record<string, string>;
  method?: string;
}

function buildSignedPayload(envelope: object, secret: string = ACCOUNT.secret): { body: Buffer; signature: string } {
  const body = Buffer.from(JSON.stringify(envelope), "utf8");
  const signature = createHmac("sha256", secret).update(body).digest("hex");
  return { body, signature };
}

async function invoke(req: FakeRequest, deliver?: (params: { account: ResolvedShadownetAccount; msg: ShadownetInboundMessage }) => Promise<void>) {
  const { body, headers, method = "POST" } = req;

  const stream = Readable.from(body) as IncomingMessage;
  Object.assign(stream, {
    method,
    url: "/shadownet/inbox",
    headers,
    socket: { remoteAddress: "127.0.0.1" } as Socket,
  });

  const captured: { status?: number; body?: string } = {};
  const res = {
    statusCode: 200,
    setHeader: () => {},
    end: (content?: string) => {
      captured.status = res.statusCode;
      if (content) captured.body = content;
    },
  } as unknown as ServerResponse;

  const handler = createWebhookHandler({
    account: ACCOUNT,
    deliver: deliver ?? (async () => {}),
    now: () => NOW_S * 1000,
  });
  await handler(stream as IncomingMessage, res);
  return captured;
}

describe("Shadownet webhook handler", () => {
  beforeEach(() => _resetWebhookStateForTest());
  afterEach(() => _resetWebhookStateForTest());

  it("rejects non-POST methods with 405", async () => {
    const result = await invoke({ method: "GET", body: Buffer.from(""), headers: {} });
    expect(result.status).toBe(405);
  });

  it("rejects requests missing X-Webhook-Signature with 401", async () => {
    const result = await invoke({
      body: Buffer.from('{"shadownet:v":"0.1"}'),
      headers: { "x-shadownet-sidecar-ts": String(NOW_S), "content-type": "application/json" },
    });
    expect(result.status).toBe(401);
    expect(result.body).toContain("missing_signature");
  });

  it("rejects bad HMAC with 401", async () => {
    const { body } = buildSignedPayload({ "shadownet:v": "0.1", event: "inbox.message", occurredAt: NOW_S, data: {} });
    const result = await invoke({
      body,
      headers: {
        "x-webhook-signature": "0".repeat(64),
        "x-shadownet-sidecar-ts": String(NOW_S),
        "content-type": "application/json",
      },
    });
    expect(result.status).toBe(401);
    expect(result.body).toContain("invalid_signature");
  });

  it("rejects requests with stale timestamps (>5 min skew) with 401", async () => {
    const stale = NOW_S - 600;
    const { body, signature } = buildSignedPayload({
      "shadownet:v": "0.1",
      event: "inbox.message",
      occurredAt: stale,
      data: { messageId: "m1", intentId: "i1", contactId: "c1" },
    });
    const result = await invoke({
      body,
      headers: {
        "x-webhook-signature": signature,
        "x-shadownet-sidecar-ts": String(stale),
        "content-type": "application/json",
      },
    });
    expect(result.status).toBe(401);
    expect(result.body).toContain("timestamp_skew");
  });

  it("ACKs unknown events (RFC-0007: receivers MUST ignore)", async () => {
    const env = {
      "shadownet:v": "0.1",
      event: "future.event",
      occurredAt: NOW_S,
      data: {},
    };
    const { body, signature } = buildSignedPayload(env);
    const result = await invoke({
      body,
      headers: {
        "x-webhook-signature": signature,
        "x-shadownet-sidecar-ts": String(NOW_S),
        "content-type": "application/json",
      },
    });
    expect(result.status).toBe(200);
    expect(result.body).toContain("ignored");
  });

  it("ACKs inbox.message and dispatches asynchronously", async () => {
    const env = {
      "shadownet:v": "0.1" as const,
      event: "inbox.message",
      occurredAt: NOW_S,
      data: { messageId: "m1", intentId: "i1", contactId: "c1" },
    };
    const { body, signature } = buildSignedPayload(env);
    // Stub social_inbox call by mocking global fetch.
    const fetchSpy = vi.fn(async () =>
      new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          result: {
            items: [
              {
                contactId: "c1",
                intentId: "i1",
                interaction: "urn:msg",
                payload: { text: "hi from peer" },
                receivedAt: NOW_S,
              },
            ],
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const original = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      const delivered: ShadownetInboundMessage[] = [];
      const result = await invoke(
        {
          body,
          headers: {
            "x-webhook-signature": signature,
            "x-shadownet-sidecar-ts": String(NOW_S),
            "content-type": "application/json",
          },
        },
        async (params) => {
          delivered.push(params.msg);
        },
      );
      expect(result.status).toBe(200);
      // Webhook handler does the social_inbox fetch + dispatch synchronously
      // inside the request handler (before returning), so by the time invoke()
      // returns, delivery has happened.
      expect(delivered).toHaveLength(1);
      expect(delivered[0]?.body).toBe("hi from peer");
      expect(delivered[0]?.contactId).toBe("c1");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("is idempotent on data.messageId — second delivery is short-circuited", async () => {
    const env = {
      "shadownet:v": "0.1",
      event: "inbox.message",
      occurredAt: NOW_S,
      data: { messageId: "m-dup", intentId: "i1", contactId: "c1" },
    };
    const { body, signature } = buildSignedPayload(env);

    const fetchSpy = vi.fn(async () =>
      new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          result: { items: [{ contactId: "c1", intentId: "i1", payload: { text: "x" }, receivedAt: NOW_S }] },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const original = globalThis.fetch;
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    try {
      let dispatchCount = 0;
      const headers = {
        "x-webhook-signature": signature,
        "x-shadownet-sidecar-ts": String(NOW_S),
        "content-type": "application/json",
      };
      await invoke({ body, headers }, async () => {
        dispatchCount += 1;
      });
      const second = await invoke({ body, headers }, async () => {
        dispatchCount += 1;
      });
      expect(dispatchCount).toBe(1);
      expect(second.body).toContain("idempotent");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("rejects malformed JSON with 400", async () => {
    const malformed = Buffer.from("not json");
    const sig = createHmac("sha256", ACCOUNT.secret).update(malformed).digest("hex");
    const result = await invoke({
      body: malformed,
      headers: {
        "x-webhook-signature": sig,
        "x-shadownet-sidecar-ts": String(NOW_S),
        "content-type": "application/json",
      },
    });
    expect(result.status).toBe(400);
    expect(result.body).toContain("invalid_json");
  });
});
