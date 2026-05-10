import { Type, type Static } from "@sinclair/typebox";

import type { ShadownetClient } from "./client";
import type { ShadownetTool, ShadownetToolResult } from "./types";

// Each entry mirrors a Pydantic model in `shadownet-py/src/shadownet/mcp/tools.py`
// and the wire dispatch in `shadownet-cloud/backend/.../sidecar/mcp_routes.py`.
// Tool names are exposed as `shadownet_<name>` to OpenClaw — no namespacing
// prefix beyond that, since OpenClaw's `api.registerTool` uses flat names.
//
// The schema-drift sentinel at
// `backend/tests/integration/test_openclaw_plugin_drift.py`
// asserts that the set of `name: "shadownet_*"` strings here matches
// `MCP_TOOL_NAMES` exactly. If you add or remove a tool in the cloud, update
// both places.

// --- TypeBox schemas (mirror shadownet.mcp.tools Pydantic models) ----------

const ContactsInput = Type.Object({
  query: Type.Optional(
    Type.String({ description: "Substring match on name or shadowname." }),
  ),
});

// social_contact_detail takes a bare `id` argument (per
// sidecar/mcp_routes.py:206 `arguments.get("id")`), not a struct.
const ContactDetailInput = Type.Object({
  id: Type.String({ description: "Contact ID from social_contacts." }),
});

const ResolveInput = Type.Object({
  shadowname: Type.String({
    description: "Shadowname to resolve via SNS, e.g. alice@sh4dow.org.",
  }),
});

const AddContactInput = Type.Object({
  shadowname: Type.String({
    description: "Shadowname to add (will be resolved if not yet known).",
  }),
  display_name: Type.Optional(
    Type.String({ description: "Optional human-readable label." }),
  ),
  grants: Type.Optional(
    Type.Array(Type.String(), {
      description: "Initial grants to apply to this contact.",
    }),
  ),
});

const SendInput = Type.Object({
  contact_id: Type.String({ description: "Contact ID from social_contacts." }),
  interaction: Type.Optional(
    Type.String({
      description:
        "Optional Interaction Profile URI; omit for free-form text payloads.",
      minLength: 1,
    }),
  ),
  intent_id: Type.Optional(
    Type.String({
      description:
        "Optional intent ID to thread this send into an existing intent.",
    }),
  ),
  payload: Type.Object(
    {},
    {
      additionalProperties: true,
      description:
        'Message payload as a JSON object. For free-form text use {"text": "..."}.',
    },
  ),
});

const InboxInput = Type.Object({
  since: Type.Optional(
    Type.Integer({
      description: "Unix epoch seconds; only messages received at or after this.",
      minimum: 0,
    }),
  ),
  interaction: Type.Optional(
    Type.String({
      description: "Filter to a specific Interaction Profile URI.",
    }),
  ),
  contact_id: Type.Optional(
    Type.String({ description: "Filter to a specific contact." }),
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
  intent_id: Type.String({
    description: "Intent ID of the inbound message being responded to.",
  }),
  payload: Type.Object(
    {},
    { additionalProperties: true, description: "Response payload." },
  ),
});

const GrantInput = Type.Object({
  contact_id: Type.String({ description: "Contact ID to grant or revoke for." }),
  grant: Type.String({
    description: "Grant name (e.g. 'inbox', 'send', 'profile').",
  }),
  allowed: Type.Boolean({ description: "True to allow, false to deny." }),
});

const IdentityInput = Type.Object({});

const SetWebhookInput = Type.Object({
  url: Type.String({
    format: "uri",
    description:
      "HTTPS URL (or http://localhost) to POST signed webhook events to.",
  }),
  secret: Type.String({
    minLength: 32,
    description:
      "Webhook signing secret (≥32 chars). Mint via the connect-page Notifications card so it's stored encrypted server-side.",
  }),
  events: Type.Optional(
    Type.Array(Type.String(), {
      description:
        "Event subset to subscribe to. Omit/null for all events (RFC-0007 default).",
    }),
  ),
});

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
      mcpName: "social_contacts",
      description:
        "List Shadownet contacts in the user's address book. Optional substring filter on name or shadowname.",
      parameters: ContactsInput,
    }),
    bind<typeof ContactDetailInput>(client, {
      name: "shadownet_contact_detail",
      mcpName: "social_contact_detail",
      description:
        "Fetch full record for one contact by ID — DID, endpoint, public key, credentials, grants, notes.",
      parameters: ContactDetailInput,
    }),
    bind<typeof ResolveInput>(client, {
      name: "shadownet_resolve",
      mcpName: "social_resolve",
      description:
        "Resolve a Shadowname (e.g. alice@sh4dow.org) to its DID, A2A endpoint, and public key via SNS, without adding it as a contact.",
      parameters: ResolveInput,
    }),
    bind<typeof AddContactInput>(client, {
      name: "shadownet_add_contact",
      mcpName: "social_add_contact",
      description:
        "Add a Shadowname to the user's contact graph. Resolves via SNS if not yet known. Returns the new contact ID.",
      parameters: AddContactInput,
    }),
    bind<typeof SendInput>(client, {
      name: "shadownet_send",
      mcpName: "social_send",
      description:
        "Send a Shadownet A2A message to a known contact. Fire-and-forget — the reply lands in shadownet_inbox asynchronously.",
      parameters: SendInput,
    }),
    bind<typeof InboxInput>(client, {
      name: "shadownet_inbox",
      mcpName: "social_inbox",
      description:
        "List recent inbound A2A messages. Optional filters: since (epoch), interaction, contact_id, limit.",
      parameters: InboxInput,
    }),
    bind<typeof RespondInput>(client, {
      name: "shadownet_respond",
      mcpName: "social_respond",
      description:
        "Respond to an inbound Shadownet message identified by its intent_id. Threads the reply onto the original intent.",
      parameters: RespondInput,
    }),
    bind<typeof GrantInput>(client, {
      name: "shadownet_grant",
      mcpName: "social_grant",
      description:
        "Allow or deny a specific grant for a contact. Per RFC-0006, denied grants cause peer messages of that type to be rejected.",
      parameters: GrantInput,
    }),
    bind<typeof IdentityInput>(client, {
      name: "shadownet_identity",
      mcpName: "social_identity",
      description:
        "Return the current Shadow's DID, Shadowname, public key, and held credentials. Useful for connection verification.",
      parameters: IdentityInput,
    }),
    bind<typeof SetWebhookInput>(client, {
      name: "shadownet_set_webhook",
      mcpName: "social_set_webhook",
      description:
        "Register or update the host-agent webhook URL the Sidecar pushes inbound activity to (RFC-0007 §Inbound notifications).",
      parameters: SetWebhookInput,
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
export type SetWebhookParams = Static<typeof SetWebhookInput>;
