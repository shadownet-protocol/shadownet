import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShadownetClient } from "../src/client";
import { sendShadownetText } from "../src/messaging";
import type { ResolvedShadownetAccount } from "../src/types";

const ACCOUNT: ResolvedShadownetAccount = {
  accountId: "default",
  enabled: true,
  endpoint: "https://example.test/u/alice/mcp",
  token: "tk-abcdef0123456789",
  secret: "0".repeat(32),
  webhookPath: "/shadownet/inbox",
  dmPolicy: "allowlist",
  allowedShadownames: ["bob@sh4dow.org"],
  rateLimitPerMinute: 60,
};

function stubClient(spy: ReturnType<typeof vi.fn>): ShadownetClient {
  const c = new ShadownetClient(ACCOUNT.endpoint, ACCOUNT.token);
  Object.defineProperty(c, "call", { value: spy, configurable: true });
  return c;
}

describe("sendShadownetText", () => {
  beforeEach(() => vi.useFakeTimers().setSystemTime(new Date("2026-05-10T12:00:00Z")));
  afterEach(() => vi.useRealTimers());

  it("routes new conversations through social_send", async () => {
    const spy = vi.fn(async () => ({ intent_id: "int-001", task_id: "tsk-001" }));
    const result = await sendShadownetText(
      { cfg: {}, to: "ctc_001", text: "hi", accountId: "default" },
      { client: stubClient(spy), resolveAccount: () => ACCOUNT },
    );
    expect(spy).toHaveBeenCalledWith("social_send", {
      contact_id: "ctc_001",
      payload: { text: "hi" },
    });
    expect(result.channel).toBe("shadownet");
    expect(result.messageId).toBe("tsk-001");
    expect(result.receipt.platformMessageIds).toEqual(["tsk-001"]);
    expect(result.receipt.threadId).toBe("int-001");
  });

  it("routes replies through social_respond when replyToId is set", async () => {
    const spy = vi.fn(async () => ({ task_id: "tsk-reply" }));
    const result = await sendShadownetText(
      { cfg: {}, to: "ctc_001", text: "ack", accountId: "default", replyToId: "int-orig" },
      { client: stubClient(spy), resolveAccount: () => ACCOUNT },
    );
    expect(spy).toHaveBeenCalledWith("social_respond", {
      intent_id: "int-orig",
      payload: { text: "ack" },
    });
    expect(result.receipt.replyToId).toBe("int-orig");
  });

  it("constructs a fallback messageId when the server omits both ids", async () => {
    const spy = vi.fn(async () => ({}));
    const result = await sendShadownetText(
      { cfg: {}, to: "ctc_001", text: "hi" },
      { client: stubClient(spy), resolveAccount: () => ACCOUNT },
    );
    expect(result.messageId).not.toBe("");
  });

  it("rejects when account is disabled or missing token", async () => {
    const disabled = { ...ACCOUNT, enabled: false };
    await expect(
      sendShadownetText(
        { cfg: {}, to: "ctc_001", text: "hi" },
        { client: stubClient(vi.fn()), resolveAccount: () => disabled },
      ),
    ).rejects.toThrow(/not configured/);
  });

  it("includes Authorization header on the underlying call (via real client)", async () => {
    const fetched: { headers: Headers; body: string } = {
      headers: new Headers(),
      body: "",
    };
    const fakeFetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const req = new Request(typeof input === "string" || input instanceof URL ? input.toString() : input.url, init);
      fetched.headers = req.headers;
      fetched.body = await req.text();
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { intent_id: "i", task_id: "t" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;
    const realClient = new ShadownetClient(ACCOUNT.endpoint, ACCOUNT.token, fakeFetch);
    await sendShadownetText(
      { cfg: {}, to: "ctc_001", text: "hi" },
      { client: realClient, resolveAccount: () => ACCOUNT },
    );
    expect(fetched.headers.get("authorization")).toBe(`Bearer ${ACCOUNT.token}`);
  });
});
