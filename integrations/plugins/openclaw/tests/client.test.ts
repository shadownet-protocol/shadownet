import { describe, expect, it, vi } from "vitest";

import { ShadownetClient } from "../src/client";

const ENDPOINT = "https://sidecar.example.test/u/alice@sh4dow.org/mcp";
const TOKEN = "tk-test-1234567890abcdef";

function makeFetch(handler: (req: Request) => Response | Promise<Response>) {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    const req = new Request(url, init);
    return handler(req);
  });
}

describe("ShadownetClient", () => {
  it("posts a JSON-RPC tools/call envelope with bearer auth", async () => {
    const captured: { headers: Headers; body: string } = { headers: new Headers(), body: "" };
    const fetchImpl = makeFetch(async (req) => {
      captured.headers = req.headers;
      captured.body = await req.text();
      return new Response(
        JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const client = new ShadownetClient(ENDPOINT, TOKEN, fetchImpl as unknown as typeof fetch);

    const result = await client.call("social_identity", {});

    expect(result).toEqual({ ok: true });
    expect(captured.headers.get("authorization")).toBe(`Bearer ${TOKEN}`);
    expect(captured.headers.get("content-type")).toBe("application/json");
    const body = JSON.parse(captured.body);
    expect(body).toMatchObject({
      jsonrpc: "2.0",
      method: "tools/call",
      params: { name: "social_identity", arguments: {} },
    });
    expect(typeof body.id).toBe("number");
  });

  it("forwards the tool's arguments verbatim", async () => {
    let captured: unknown = null;
    const fetchImpl = makeFetch(async (req) => {
      captured = JSON.parse(await req.text()).params;
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result: null }), { status: 200 });
    });
    const client = new ShadownetClient(ENDPOINT, TOKEN, fetchImpl as unknown as typeof fetch);

    await client.call("social_send", {
      contact_id: "ctc_001",
      payload: { text: "hello" },
    });

    expect(captured).toEqual({
      name: "social_send",
      arguments: { contact_id: "ctc_001", payload: { text: "hello" } },
    });
  });

  it("throws on non-2xx HTTP", async () => {
    const fetchImpl = makeFetch(async () =>
      new Response("forbidden", { status: 403 }),
    );
    const client = new ShadownetClient(ENDPOINT, TOKEN, fetchImpl as unknown as typeof fetch);

    await expect(client.call("social_identity", {})).rejects.toThrow(
      /HTTP 403.*forbidden/,
    );
  });

  it("throws on JSON-RPC error envelope", async () => {
    const fetchImpl = makeFetch(async () =>
      new Response(
        JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          error: { code: -32602, message: "invalid params" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = new ShadownetClient(ENDPOINT, TOKEN, fetchImpl as unknown as typeof fetch);

    await expect(client.call("social_send", {})).rejects.toThrow(
      /RPC -32602: invalid params/,
    );
  });

  it("increments the JSON-RPC id per call", async () => {
    const ids: number[] = [];
    const fetchImpl = makeFetch(async (req) => {
      ids.push(JSON.parse(await req.text()).id);
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1, result: null }), { status: 200 });
    });
    const client = new ShadownetClient(ENDPOINT, TOKEN, fetchImpl as unknown as typeof fetch);

    await client.call("social_identity", {});
    await client.call("social_identity", {});
    await client.call("social_identity", {});

    expect(ids).toEqual([1, 2, 3]);
  });
});
