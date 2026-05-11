// Account configuration for the Shadownet channel plugin.
// v1 supports a single default account; the multi-account helpers exist so
// future versions can grow without an API rewrite.

import { Type, type Static } from "@sinclair/typebox";

import type { ResolvedShadownetAccount, ShadownetDmPolicy } from "./types";

const DEFAULT_ACCOUNT_ID = "default";
const DEFAULT_WEBHOOK_PATH = "/shadownet/inbox";
const DEFAULT_RATE_LIMIT = 60;

export const ShadownetChannelConfigSchema = Type.Object(
  {
    enabled: Type.Optional(Type.Boolean({ description: "Disable to keep the account configured but stop the webhook route." })),
    endpoint: Type.String({
      // `format: "uri"` is intentionally omitted — TypeBox 0.34's default
      // FormatRegistry doesn't include it, and we validate the URL at
      // request time via fetch() in src/client.ts.
      minLength: 1,
      description:
        "Per-tenant MCP endpoint, e.g. https://<your-sidecar>/u/<your-shadowname>/mcp. RFC-0008-compliant Sidecars serve a copy-pasteable snippet at https://<your-sidecar>/connect/openclaw.",
    }),
    token: Type.String({
      minLength: 16,
      description: "Bearer token for the MCP endpoint.",
    }),
    secret: Type.String({
      minLength: 32,
      description: "Webhook signing secret — must match the value shown on the connect page when the webhook was minted.",
    }),
    webhookPath: Type.Optional(
      Type.String({
        description: `Local path the plugin registers for inbound webhook deliveries (default ${DEFAULT_WEBHOOK_PATH}).`,
      }),
    ),
    dmPolicy: Type.Optional(
      Type.Union([Type.Literal("allowlist"), Type.Literal("open")], {
        description: "allowlist (default) restricts inbound to allowedShadownames; open accepts any verified peer.",
      }),
    ),
    allowedShadownames: Type.Optional(
      Type.Array(Type.String(), {
        description: "Shadownames (alice@sh4dow.org form) allowed to message this Shadow. Empty + dmPolicy=allowlist rejects all.",
      }),
    ),
    rateLimitPerMinute: Type.Optional(
      Type.Integer({
        minimum: 1,
        maximum: 1000,
        description: `Max inbound webhook deliveries per minute (default ${DEFAULT_RATE_LIMIT}).`,
      }),
    ),
  },
  { additionalProperties: false },
);

export type ShadownetChannelConfig = Static<typeof ShadownetChannelConfigSchema>;

interface ConfigSource {
  channels?: {
    shadownet?: Record<string, unknown> & {
      accounts?: Record<string, Record<string, unknown>>;
    };
  };
}

export function listAccountIds(cfg: unknown): string[] {
  const root = (cfg as ConfigSource | undefined)?.channels?.shadownet;
  if (!root) return [];
  const accounts = root.accounts;
  if (accounts && typeof accounts === "object") {
    return Object.keys(accounts);
  }
  // Single-account default — `channels.shadownet.{endpoint,token,...}` directly.
  return [DEFAULT_ACCOUNT_ID];
}

function readAccountSection(cfg: unknown, accountId: string | null | undefined): Record<string, unknown> | undefined {
  const root = (cfg as ConfigSource | undefined)?.channels?.shadownet;
  if (!root) return undefined;
  const id = accountId ?? DEFAULT_ACCOUNT_ID;
  if (root.accounts && typeof root.accounts === "object" && id in root.accounts) {
    return root.accounts[id];
  }
  if (id === DEFAULT_ACCOUNT_ID) {
    // Single-account fallback — strip the `accounts` key (if it exists) so
    // downstream consumers see a clean account object.
    const { accounts: _, ...rest } = root;
    return rest as Record<string, unknown>;
  }
  return undefined;
}

export function resolveAccount(
  cfg: unknown,
  accountId?: string | null,
): ResolvedShadownetAccount {
  const id = accountId ?? DEFAULT_ACCOUNT_ID;
  const section = readAccountSection(cfg, id) ?? {};
  const dmPolicy = (section.dmPolicy as ShadownetDmPolicy | undefined) ?? "allowlist";
  return {
    accountId: id,
    enabled: section.enabled !== false,
    endpoint: typeof section.endpoint === "string" ? section.endpoint : "",
    token: typeof section.token === "string" ? section.token : "",
    secret: typeof section.secret === "string" ? section.secret : "",
    webhookPath:
      typeof section.webhookPath === "string" && section.webhookPath ? section.webhookPath : DEFAULT_WEBHOOK_PATH,
    dmPolicy,
    allowedShadownames: Array.isArray(section.allowedShadownames)
      ? section.allowedShadownames.filter((s): s is string => typeof s === "string")
      : [],
    rateLimitPerMinute:
      typeof section.rateLimitPerMinute === "number" && section.rateLimitPerMinute > 0
        ? section.rateLimitPerMinute
        : DEFAULT_RATE_LIMIT,
  };
}

export const SHADOWNET_DEFAULTS = {
  accountId: DEFAULT_ACCOUNT_ID,
  webhookPath: DEFAULT_WEBHOOK_PATH,
  rateLimitPerMinute: DEFAULT_RATE_LIMIT,
} as const;
