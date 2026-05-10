import { describe, expect, it, vi } from "vitest";

import { ShadownetClient } from "../src/client";
import { tools } from "../src/tools/tools";

const EXPECTED_NAMES = [
  "shadownet_contacts",
  "shadownet_contact_detail",
  "shadownet_resolve",
  "shadownet_add_contact",
  "shadownet_send",
  "shadownet_inbox",
  "shadownet_respond",
  "shadownet_grant",
  "shadownet_identity",
  "shadownet_set_webhook",
] as const;

const EXPECTED_MCP_NAMES: Record<(typeof EXPECTED_NAMES)[number], string> = {
  shadownet_contacts: "social_contacts",
  shadownet_contact_detail: "social_contact_detail",
  shadownet_resolve: "social_resolve",
  shadownet_add_contact: "social_add_contact",
  shadownet_send: "social_send",
  shadownet_inbox: "social_inbox",
  shadownet_respond: "social_respond",
  shadownet_grant: "social_grant",
  shadownet_identity: "social_identity",
  shadownet_set_webhook: "social_set_webhook",
};

function stubClient(): ShadownetClient {
  // Real ShadownetClient with a fetch impl that won't be invoked unless we
  // execute() — tools.test only inspects shape and does targeted execute.
  return new ShadownetClient("http://stub/mcp", "stub-token", (() => {
    throw new Error("fetch should not have been called");
  }) as unknown as typeof fetch);
}

describe("tool registry", () => {
  it("returns exactly the 10 RFC-0007 tools, snake_case shadownet_*", () => {
    const list = tools(stubClient());
    expect(list.map((t) => t.name).sort()).toEqual([...EXPECTED_NAMES].sort());
  });

  it("each tool has a non-empty description and a TypeBox object schema", () => {
    for (const tool of tools(stubClient())) {
      expect(tool.description.length).toBeGreaterThan(0);
      // TypeBox `Type.Object` schemas always have type === "object".
      const schema = tool.parameters as { type?: string };
      expect(schema.type).toBe("object");
    }
  });

  it("each tool has a unique name matching ^shadownet_[a-z_]+$", () => {
    const list = tools(stubClient());
    const names = list.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
    for (const name of names) {
      expect(name).toMatch(/^shadownet_[a-z_]+$/);
    }
  });

  it("execute() forwards to the matching MCP tool name with the given params", async () => {
    const callSpy = vi.fn(async () => ({ ok: true }));
    const client = stubClient();
    // Patch the call method directly so we can capture invocations.
    Object.defineProperty(client, "call", { value: callSpy, configurable: true });

    const list = tools(client);
    for (const tool of list) {
      callSpy.mockClear();
      const params = { sample: "value" };
      const result = await tool.execute("toolu_test", params);
      expect(result).toMatchObject({ content: expect.any(Array) });
      const expectedMcpName = EXPECTED_MCP_NAMES[tool.name as keyof typeof EXPECTED_MCP_NAMES];
      expect(callSpy).toHaveBeenCalledWith(expectedMcpName, params);
    }
  });

  it("execute() returns isError=true on client failure", async () => {
    const client = stubClient();
    const callSpy = vi.fn(async () => {
      throw new Error("boom");
    });
    Object.defineProperty(client, "call", { value: callSpy, configurable: true });

    const tool = tools(client).find((t) => t.name === "shadownet_identity");
    expect(tool).toBeDefined();
    const result = await tool!.execute("toolu_test", {});
    expect(result.isError).toBe(true);
    expect(result.content[0]).toMatchObject({ type: "text", text: "boom" });
  });
});
