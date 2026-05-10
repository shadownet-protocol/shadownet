// Local type stubs for the OpenClaw plugin SDK surface this package uses.
//
// Why this exists: depending on `openclaw` as a real (dev) npm package pulls
// in ~534 transitive packages — far too much footprint for a plugin that
// only needs compile-time types and ships its runtime through OpenClaw
// itself. By declaring just the SDK paths we import, we get type-checking
// without the install. End-users see `openclaw` in our `peerDependencies`
// (so their gateway provides the real implementation at runtime) and our
// CI runs `pnpm install` without the openclaw devDep.
//
// Each module declared here matches the `exports` map of openclaw@^2026.5
// for the subpaths actually imported by `src/`. Types are pragmatic — we
// type the things we depend on the shape of, and use `unknown` for the
// rest. If a future SDK release tightens these, we can drop this file and
// pin a real openclaw devDep.

declare module "openclaw/plugin-sdk/channel-entry-contract" {
  export interface BundledChannelEntryParams {
    id: string;
    name: string;
    description: string;
    importMetaUrl: string;
    plugin: { specifier: string; exportName: string };
    runtime?: { specifier: string; exportName: string };
    secrets?: { specifier: string; exportName: string };
    registerCliMetadata?: (api: unknown) => void;
    registerFull?: (api: unknown) => void;
  }
  export function defineBundledChannelEntry(
    params: BundledChannelEntryParams,
  ): BundledChannelEntryParams;
}

declare module "openclaw/plugin-sdk/channel-core" {
  export interface ChannelPlugin<TResolvedAccount = unknown> {
    id: string;
    meta?: unknown;
    setup?: unknown;
    capabilities?: unknown;
    setupWizard?: unknown;
    commands?: unknown;
    doctor?: unknown;
    agentPrompt?: unknown;
    streaming?: unknown;
    reload?: unknown;
    gatewayMethods?: unknown;
    configSchema?: unknown;
    config?: unknown;
    security?: unknown;
    groups?: unknown;
    pairing?: unknown;
    threading?: unknown;
    outbound?: unknown;
    // Runtime fields the chat-channel helper layers on. Present on the
    // returned plugin even though they're not in the published
    // CreateChannelPluginBaseOptions surface.
    [key: string]: unknown;
  }

  export interface ChannelPluginBase<TResolvedAccount = unknown> {
    id: string;
    setup: unknown;
    [key: string]: unknown;
  }

  export interface CreateChannelPluginBaseOptions<TResolvedAccount = unknown> {
    id: string;
    meta?: unknown;
    setupWizard?: unknown;
    capabilities?: unknown;
    commands?: unknown;
    doctor?: unknown;
    agentPrompt?: unknown;
    streaming?: unknown;
    reload?: unknown;
    gatewayMethods?: unknown;
    configSchema?: unknown;
    config?: unknown;
    security?: unknown;
    setup: unknown;
    groups?: unknown;
  }

  export interface CreateChatChannelPluginParams<TResolvedAccount = unknown> {
    base: ChannelPluginBase<TResolvedAccount>;
    security?: unknown;
    pairing?: unknown;
    threading?: unknown;
    outbound?: unknown;
  }

  export function createChannelPluginBase<TResolvedAccount = unknown>(
    params: CreateChannelPluginBaseOptions<TResolvedAccount>,
  ): ChannelPluginBase<TResolvedAccount>;

  export function createChatChannelPlugin<TResolvedAccount = unknown>(
    params: CreateChatChannelPluginParams<TResolvedAccount>,
  ): ChannelPlugin<TResolvedAccount>;
}

declare module "openclaw/plugin-sdk/channel-lifecycle" {
  export function waitUntilAbort(
    signal: AbortSignal,
    onAbort?: () => void | Promise<void>,
  ): Promise<void>;
}

declare module "openclaw/plugin-sdk/webhook-ingress" {
  import type { IncomingMessage, ServerResponse } from "node:http";

  export interface PluginHttpRouteHandler {
    (req: IncomingMessage, res: ServerResponse): Promise<boolean | void> | boolean | void;
  }

  export interface RegisterPluginHttpRouteParams {
    path?: string | null | undefined;
    handler: PluginHttpRouteHandler;
    auth: "gateway" | "plugin";
    pluginId?: string;
    accountId?: string;
    log?: (message: string) => void;
    replaceExisting?: boolean;
    fallbackPath?: string | null;
    match?: "exact" | "prefix";
  }

  export function registerPluginHttpRoute(
    params: RegisterPluginHttpRouteParams,
  ): () => void;

  export interface ReadRequestBodyOptions {
    maxBytes?: number;
    timeoutMs?: number;
  }

  export function readRequestBodyWithLimit(
    req: IncomingMessage,
    opts?: ReadRequestBodyOptions,
  ): Promise<string>;

  export interface RequestBodyLimitError extends Error {
    statusCode: number;
    code: string;
  }

  export function isRequestBodyLimitError(err: unknown): err is RequestBodyLimitError;
  export function requestBodyErrorToText(code: string): string;

  export interface WebhookInFlightLimiter {
    clear(): void;
  }
  export function createWebhookInFlightLimiter(): WebhookInFlightLimiter;

  export interface WebhookRequestPipelineLifecycle {
    ok: boolean;
    release(): void;
  }
  export function beginWebhookRequestPipelineOrReject(params: {
    req: IncomingMessage;
    res: ServerResponse;
    inFlightLimiter: WebhookInFlightLimiter;
    inFlightKey: string;
  }): WebhookRequestPipelineLifecycle;
}

declare module "openclaw/plugin-sdk/directory-runtime" {
  export function createEmptyChannelDirectoryAdapter(): unknown;
}

declare module "openclaw/plugin-sdk/channel-config-helpers" {
  export function createScopedDmSecurityResolver<T = unknown>(params: unknown): unknown;
  export function createHybridChannelConfigAdapter<T = unknown>(params: unknown): unknown;
}
