// Channel plugin definition. Composes the `createChatChannelPlugin` builder
// from the OpenClaw SDK with our account, security, outbound, and gateway
// adapters. The result is exported as `shadownetPlugin` and re-exported by
// `channel-plugin-api.ts` for the bundled-channel-entry contract.

import {
  createChatChannelPlugin,
  createChannelPluginBase,
} from "openclaw/plugin-sdk/channel-core";
import { waitUntilAbort } from "openclaw/plugin-sdk/channel-lifecycle";

import {
  ShadownetChannelConfigSchema,
  resolveAccount,
} from "./account";
import { sendShadownetText } from "./messaging";
import { describeDmPolicyDecision } from "./security";
import type { ResolvedShadownetAccount } from "./types";
import { registerShadownetWebhookRoute } from "./webhook";

const CHANNEL_ID = "shadownet";

interface GatewayCtx {
  cfg: unknown;
  accountId: string;
  abortSignal: AbortSignal;
  log?: { info?: (m: string) => void; warn?: (m: string) => void; error?: (m: string) => void };
}

// `createChannelPluginBase` in the published SDK only accepts a narrow field
// set; the richer `messaging`/`directory`/`gateway`/`message` fields the chat
// channel actually uses are attached at runtime and the SDK type-checks with a
// final `as` cast. Bundled extensions (synology-chat, slack, matrix) follow
// the same pattern. We mirror it here to stay forward-compatible with the
// fuller types when they're published.
const base = createChannelPluginBase<ResolvedShadownetAccount>({
  id: CHANNEL_ID,
  meta: {
    id: CHANNEL_ID,
    label: "Shadownet",
    selectionLabel: "Shadownet (A2A)",
    detailLabel: "Shadownet",
    docsPath: "/channels/shadownet",
    blurb: "Identity-anchored A2A network",
    order: 95,
  },
  capabilities: {
    chatTypes: ["direct" as const],
    media: false,
    threads: false,
    reactions: false,
    edit: false,
    unsend: false,
    reply: false,
    effects: false,
    blockStreaming: false,
  },
  reload: { configPrefixes: [`channels.${CHANNEL_ID}`] },
  configSchema: ShadownetChannelConfigSchema as unknown as Parameters<
    typeof createChannelPluginBase<ResolvedShadownetAccount>
  >[0]["configSchema"],
  setup: {
    listAccountIds: (cfg: unknown) => {
      // Lazy-import to avoid a cycle.
      const mod = require("./account") as typeof import("./account");
      return mod.listAccountIds(cfg);
    },
    resolveAccount,
    inspectAccount: (cfg: unknown, accountId?: string | null) => {
      const account = resolveAccount(cfg, accountId);
      return {
        enabled: account.enabled && Boolean(account.endpoint) && Boolean(account.token),
        configured: Boolean(account.endpoint && account.token && account.secret),
        tokenStatus: account.token ? "set" : "missing",
      };
    },
  } as unknown as Parameters<typeof createChannelPluginBase<ResolvedShadownetAccount>>[0]["setup"],
});

// Cast destination type: the resulting plugin object accepts the additional
// runtime fields (gateway, outbound, message, security) that the SDK consumes
// internally during channel orchestration even though the published types
// don't surface them on `CreateChannelPluginBaseOptions`. The chat-channel
// helper layers them on:
type ShadownetChannelPlugin = ReturnType<
  typeof createChatChannelPlugin<ResolvedShadownetAccount>
> & {
  gateway: {
    startAccount: (ctx: GatewayCtx) => Promise<unknown>;
    stopAccount: (ctx: GatewayCtx) => Promise<void>;
  };
};

export const shadownetPlugin = createChatChannelPlugin<ResolvedShadownetAccount>({
  base: {
    ...(base as ShadownetChannelPlugin),
    gateway: {
      startAccount: async (ctx: GatewayCtx) => {
        const account = resolveAccount(ctx.cfg, ctx.accountId);
        if (!account.enabled || !account.endpoint || !account.token || !account.secret) {
          ctx.log?.warn?.(
            `shadownet: account ${ctx.accountId} not fully configured — skipping route registration`,
          );
          return waitUntilAbort(ctx.abortSignal);
        }
        ctx.log?.info?.(
          `shadownet: account ${ctx.accountId} starting (path: ${account.webhookPath})`,
        );
        const unregister = registerShadownetWebhookRoute({ account, log: ctx.log });
        return waitUntilAbort(ctx.abortSignal, () => {
          ctx.log?.info?.(`shadownet: account ${ctx.accountId} stopping`);
          unregister();
        });
      },
      stopAccount: async (ctx: GatewayCtx) => {
        ctx.log?.info?.(`shadownet: account ${ctx.accountId} stopped`);
      },
    },
  } as unknown as NonNullable<
    Parameters<typeof createChatChannelPlugin<ResolvedShadownetAccount>>[0]["base"]
  >,
  security: {
    resolveDmPolicy: (params: { account: ResolvedShadownetAccount }) => ({
      policy: params.account.dmPolicy,
      allowFrom: [...params.account.allowedShadownames],
    }),
    collectWarnings: (params: { account: ResolvedShadownetAccount }) => {
      const messages: string[] = [];
      if (
        params.account.dmPolicy === "allowlist" &&
        params.account.allowedShadownames.length === 0
      ) {
        messages.push(
          "- Shadownet: dmPolicy=allowlist with no allowedShadownames; all senders rejected.",
        );
      }
      return messages;
    },
  } as unknown as NonNullable<
    Parameters<typeof createChatChannelPlugin<ResolvedShadownetAccount>>[0]["security"]
  >,
  outbound: {
    deliveryMode: "gateway" as const,
    textChunkLimit: 4000,
    sendText: async (ctx: {
      cfg: unknown;
      to: string;
      text: string;
      accountId?: string | null;
      replyToId?: string;
      threadId?: string;
    }) => sendShadownetText(ctx, { resolveAccount }),
  } as unknown as NonNullable<
    Parameters<typeof createChatChannelPlugin<ResolvedShadownetAccount>>[0]["outbound"]
  >,
}) as ShadownetChannelPlugin;

// Convenience for tests / for the security check.
export { describeDmPolicyDecision };
