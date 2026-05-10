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
