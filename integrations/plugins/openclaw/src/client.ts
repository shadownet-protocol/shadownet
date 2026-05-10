import type { JsonRpcResponse } from "./types";

// Thin JSON-RPC client over Streamable HTTP for the Shadownet sidecar's MCP
// endpoint at `/u/<shadowname>/mcp`. Each call() performs a `tools/call` RPC
// under the user-supplied bearer token; the server's response is unwrapped to
// either `result` or thrown as an Error per the JSON-RPC error envelope.

export class ShadownetClient {
  private idCounter = 0;

  constructor(
    private readonly endpoint: string,
    private readonly token: string,
    // Allow tests to inject a fetch impl. Defaults to the global one.
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async call(toolName: string, args: Record<string, unknown>): Promise<unknown> {
    const id = ++this.idCounter;
    const response = await this.fetchImpl(this.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.token}`,
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id,
        method: "tools/call",
        params: { name: toolName, arguments: args },
      }),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`shadownet MCP HTTP ${response.status}: ${text.slice(0, 200)}`);
    }
    const body = (await response.json()) as JsonRpcResponse;
    if (body.error) {
      throw new Error(`shadownet RPC ${body.error.code}: ${body.error.message}`);
    }
    return body.result;
  }
}
