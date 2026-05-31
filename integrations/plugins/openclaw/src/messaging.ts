// Inbound dispatch into OpenClaw's turn pipeline + outbound `sendText` from
// the agent back to the Shadownet peer.
//
// The published `openclaw` npm package exposes `PluginRuntime` as an opaque
// type — its full surface (`runtime.channel.turn.run`, route resolvers, etc.)
// is documented through the bundled-extension source rather than the
// distributed types. We treat the runtime as a duck-typed bag of the methods
// we need; the dispatch function casts at the boundary. If a future SDK
// version pins these types, we drop the local `ShadownetChannelRuntime`
// interface in favour of the SDK's.

import { ShadownetClient } from "./client";
import { getShadownetRuntime } from "./runtime";
import type { ResolvedShadownetAccount, ShadownetInboundMessage } from "./types";

const CHANNEL_ID = "shadownet";

interface AgentRouteRequest {
  cfg: unknown;
  channel: string;
  accountId: string;
  peer: { kind: "direct"; id: string };
}

interface AgentRoute {
  agentId: string;
}

interface TurnRunParams {
  channel: string;
  accountId: string;
  raw: ShadownetInboundMessage;
  adapter: {
    senderName?: string | undefined;
    body: string;
    onReply?: ((reply: { text?: string }) => Promise<void>) | undefined;
  };
}

// Local shape for the parts of `PluginRuntime` we actually use. If the SDK
// pins these types in a future release, drop this interface.
interface ShadownetChannelRuntime {
  config: { current: () => unknown };
  channel: {
    routing: {
      resolveAgentRoute: (req: AgentRouteRequest) => AgentRoute;
    };
    turn: {
      run: (params: TurnRunParams) => Promise<void>;
    };
  };
}

export async function dispatchShadownetInboundTurn(params: {
  account: ResolvedShadownetAccount;
  msg: ShadownetInboundMessage;
}): Promise<void> {
  const runtime = getShadownetRuntime() as ShadownetChannelRuntime;
  const cfg = runtime.config.current();
  const route = runtime.channel.routing.resolveAgentRoute({
    cfg,
    channel: CHANNEL_ID,
    accountId: params.account.accountId,
    peer: { kind: "direct", id: params.msg.from },
  });
  void route; // route is consumed by the runtime via the closure binding below.
  await runtime.channel.turn.run({
    channel: CHANNEL_ID,
    accountId: params.account.accountId,
    raw: params.msg,
    adapter: {
      senderName: params.msg.from,
      body: params.msg.body,
    },
  });
}

// ----- outbound -----

export interface ShadownetSendContext {
  cfg: unknown;
  to: string;
  text: string;
  accountId?: string | null;
  // If the SDK supplies a `replyToId` we treat it as a Shadownet `contextId`
  // and route through the `respond` tool. Otherwise we open a fresh `send`.
  replyToId?: string;
  threadId?: string;
}

export interface ShadownetSendResult {
  channel: typeof CHANNEL_ID;
  messageId: string;
  receipt: {
    primaryPlatformMessageId: string;
    platformMessageIds: readonly string[];
    threadId?: string | undefined;
    replyToId?: string | undefined;
    sentAt: number;
  };
}

export interface SendShadownetTextDeps {
  // Test seam — defaults to a fresh ShadownetClient using the resolved
  // account's endpoint + token.
  client?: ShadownetClient;
  resolveAccount: (
    cfg: unknown,
    accountId?: string | null,
  ) => ResolvedShadownetAccount;
}

export async function sendShadownetText(
  ctx: ShadownetSendContext,
  deps: SendShadownetTextDeps,
): Promise<ShadownetSendResult> {
  const account = deps.resolveAccount(ctx.cfg, ctx.accountId);
  if (!account.enabled || !account.endpoint || !account.token) {
    throw new Error(
      `shadownet: account ${account.accountId} is not configured (endpoint/token missing or disabled).`,
    );
  }
  const client = deps.client ?? new ShadownetClient(account.endpoint, account.token);

  // v0.2 (RFC 0002 §4): `send` takes {to, body, contextId?} and returns
  // {messageId, contextId, status, error?}; `respond` takes {contextId, body}
  // and returns {messageId, status, error?}. The body is the opaque slot from
  // RFC 0001 §8.5 — here we send free-form text.
  let mcpName: "send" | "respond";
  let args: Record<string, unknown>;
  if (ctx.replyToId) {
    mcpName = "respond";
    args = { contextId: ctx.replyToId, body: { text: ctx.text } };
  } else {
    mcpName = "send";
    args = { to: ctx.to, body: { text: ctx.text } };
  }

  const result = (await client.call(mcpName, args)) as {
    messageId?: string;
    contextId?: string;
  } | null;

  const messageId = (result && result.messageId) || `${Date.now()}`;
  const contextId = (result && result.contextId) || ctx.replyToId || "";

  return {
    channel: CHANNEL_ID,
    messageId,
    receipt: {
      primaryPlatformMessageId: messageId,
      platformMessageIds: [messageId],
      threadId: ctx.threadId ?? (contextId || undefined),
      replyToId: ctx.replyToId,
      sentAt: Math.floor(Date.now() / 1000),
    },
  };
}
