import { Type, type Static } from "@sinclair/typebox";

import type { ShadownetClient } from "../client";
import type { ShadownetTool, ShadownetToolResult } from "../types";

// Each entry mirrors a Pydantic model in `python-sdk/src/shadownet/mcp/tools.py`
// (RFC 0002 §4) and the wire dispatch in the Sidecar's MCP routes. Tool names
// are exposed as `shadownet_<name>` to OpenClaw — no namespacing prefix beyond
// that, since OpenClaw's `api.registerTool` uses flat names. The `mcpName` is
// the v0.2 Sidecar tool name (no `social_` prefix per RFC 0002 §4).
//
// `params` are forwarded verbatim to the MCP tool, so each schema below is the
// v0.2 wire argument shape. Identifiers (`name`, `to`, `contact`) accept a
// Shadowname (`alice@sh4dow.org`) or a `shadow://` URI (RFC 0001 §3.3).
//
// The schema-drift sentinel in the cloud asserts the set of `name: "shadownet_*"`
// strings here matches the Sidecar's registered tool set. The OpenClaw-facing
// names are unchanged by the v0.2 migration; only `mcpName` + arg shapes move.

// --- TypeBox schemas (mirror shadownet.mcp.tools Pydantic models) ----------

const BodySlot = Type.Object(
  {
    text: Type.Optional(Type.String({ description: "Free-form message text." })),
    intent: Type.Optional(
      Type.String({ description: "Intent URI, e.g. urn:shadownet:intent:coordinate_v1." }),
    ),
    data: Type.Optional(
      Type.Object({}, { additionalProperties: true, description: "Intent-profile payload." }),
    ),
  },
  { description: "Envelope body (RFC 0001 §8.5)." },
);

const ContactsInput = Type.Object({
  query: Type.Optional(
    Type.String({ description: "Substring match on identifier or displayName." }),
  ),
});

const ContactDetailInput = Type.Object({
  name: Type.String({
    description: "Contact identifier (Shadowname or shadow:// URI).",
  }),
});

const ResolveInput = Type.Object({
  name: Type.String({
    description: "Identifier to resolve (Shadowname e.g. alice@sh4dow.org, or shadow:// URI).",
  }),
});

const AddContactInput = Type.Object({
  name: Type.String({
    description: "Identifier to add (Shadowname or shadow:// URI; resolved if not yet known).",
  }),
  displayName: Type.Optional(
    Type.String({ description: "Optional human-readable label." }),
  ),
  grants: Type.Optional(
    Type.Array(Type.String(), {
      description: "Initial grants to apply to this contact (defaults to ['messaging']).",
    }),
  ),
});

const SendInput = Type.Object({
  to: Type.String({
    description: "Recipient identifier (Shadowname or shadow:// URI).",
  }),
  body: BodySlot,
  contextId: Type.Optional(
    Type.String({
      description: "Existing thread to send into; omit to open a new context.",
    }),
  ),
});

const InboxInput = Type.Object({
  since: Type.Optional(
    Type.String({
      description: "Opaque cursor from a prior inbox call's nextSince. Do not parse.",
    }),
  ),
  intent: Type.Optional(
    Type.String({ description: "Filter to envelopes carrying this intent URI." }),
  ),
  contact: Type.Optional(
    Type.String({ description: "Filter to a specific contact identifier." }),
  ),
  includeReview: Type.Optional(
    Type.Boolean({ description: "Include stranger_review items (default false)." }),
  ),
  limit: Type.Optional(
    Type.Integer({
      description: "Max items to return (default server-side).",
      minimum: 1,
      maximum: 1000,
    }),
  ),
});

const RespondInput = Type.Object({
  contextId: Type.String({
    description: "Context ID of the thread being responded to.",
  }),
  body: BodySlot,
});

const GrantInput = Type.Object({
  name: Type.String({ description: "Contact identifier to grant or revoke for." }),
  grant: Type.String({
    description: "Grant name (e.g. 'messaging').",
  }),
  allowed: Type.Boolean({ description: "True to allow, false to deny." }),
});

const IdentityInput = Type.Object({});

// --- tool registry ---------------------------------------------------------

interface InternalTool<S> {
  name: string;
  mcpName: string;
  description: string;
  parameters: S;
}

function bind<S extends object>(
  client: ShadownetClient,
  spec: InternalTool<S>,
): ShadownetTool {
  return {
    name: spec.name,
    description: spec.description,
    parameters: spec.parameters,
    async execute(_id, params): Promise<ShadownetToolResult> {
      try {
        const result = await client.call(spec.mcpName, params);
        return {
          content: [{ type: "text", text: JSON.stringify(result) }],
        };
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text", text: message }],
          isError: true,
        };
      }
    },
  };
}

export function tools(client: ShadownetClient): ShadownetTool[] {
  return [
    bind<typeof ContactsInput>(client, {
      name: "shadownet_contacts",
      mcpName: "contacts",
      description:
        "List Shadownet contacts in the user's address book. Optional substring filter on identifier or displayName.",
      parameters: ContactsInput,
    }),
    bind<typeof ContactDetailInput>(client, {
      name: "shadownet_contact_detail",
      mcpName: "contact_detail",
      description:
        "Fetch the full record for one contact — endpoint, public key, credentials, grants, profile.",
      parameters: ContactDetailInput,
    }),
    bind<typeof ResolveInput>(client, {
      name: "shadownet_resolve",
      mcpName: "resolve",
      description:
        "Resolve an identifier (Shadowname e.g. alice@sh4dow.org, or shadow:// URI) to its A2A endpoint and public key, without adding it as a contact.",
      parameters: ResolveInput,
    }),
    bind<typeof AddContactInput>(client, {
      name: "shadownet_add_contact",
      mcpName: "add_contact",
      description:
        "Add an identifier to the user's contact graph. Resolves it if not yet known.",
      parameters: AddContactInput,
    }),
    bind<typeof SendInput>(client, {
      name: "shadownet_send",
      mcpName: "send",
      description:
        "Send a Shadownet A2A envelope to a contact. Fire-and-forget — the reply lands in shadownet_inbox asynchronously.",
      parameters: SendInput,
    }),
    bind<typeof InboxInput>(client, {
      name: "shadownet_inbox",
      mcpName: "inbox",
      description:
        "List recent inbound A2A messages with full body content. Optional filters: since (opaque cursor), intent, contact, includeReview, limit.",
      parameters: InboxInput,
    }),
    bind<typeof RespondInput>(client, {
      name: "shadownet_respond",
      mcpName: "respond",
      description:
        "Respond within an existing thread identified by its contextId.",
      parameters: RespondInput,
    }),
    bind<typeof GrantInput>(client, {
      name: "shadownet_grant",
      mcpName: "grant",
      description:
        "Allow or deny a specific grant for a contact. Denied grants cause peer messages relying on that grant to be rejected with a policy error (RFC 0001 §8.8).",
      parameters: GrantInput,
    }),
    bind<typeof IdentityInput>(client, {
      name: "shadownet_identity",
      mcpName: "identity",
      description:
        "Return the current Shadow's Shadowname and/or direct URI, public key, and held credentials. Useful for connection verification.",
      parameters: IdentityInput,
    }),
  ];
}

// Re-export schema types so tests + the entry point can introspect them.
export type ContactsParams = Static<typeof ContactsInput>;
export type ContactDetailParams = Static<typeof ContactDetailInput>;
export type ResolveParams = Static<typeof ResolveInput>;
export type AddContactParams = Static<typeof AddContactInput>;
export type SendParams = Static<typeof SendInput>;
export type InboxParams = Static<typeof InboxInput>;
export type RespondParams = Static<typeof RespondInput>;
export type GrantParams = Static<typeof GrantInput>;
export type IdentityParams = Static<typeof IdentityInput>;
