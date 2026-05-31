// SPDX-License-Identifier: MIT

// Package wellknown holds the named constants for Shadownet v0.2 wire paths,
// JWS typ values, content types, and HTTP headers. Centralizing them here
// keeps the cross-cutting strings discoverable in one file and makes
// "search and replace" reliable when the spec moves.
//
// Spec: shadownet-specs/rfcs/0001-shadownet.md v0.2 (commit 51cb6c8).
package wellknown

// Wire path components.
const (
	// IdentityPathPrefix is the prefix the Provider serves AgentCards under
	// (RFC 0001 §5.2). Full path: <ep>/identity/<local>.
	IdentityPathPrefix = "/identity/"

	// IssuePath is the Issuer's CSR-in / credential-out endpoint for the
	// domain-identified mode (RFC 0001 §6.5).
	IssuePath = "/.well-known/shadownet/issue"

	// StatusPathPrefix is the Issuer's status bitstring prefix for the
	// domain-identified mode (RFC 0001 §6.4). Full path:
	// /.well-known/shadownet/status/<epoch>.
	StatusPathPrefix = "/.well-known/shadownet/status/"

	// DirectAgentCardPath is A2A's standard well-known AgentCard URI
	// (A2A §8.2.1), used by direct-mode Shadows and by keyed-Hub Issuers
	// to self-serve their card.
	DirectAgentCardPath = "/.well-known/agent-card.json"
)

// JWS `typ` header values (RFC 0001 §2 naming table; RFC 8417 +jwt suffix).
const (
	TypShadownetEnvJWT  = "shadownet-env+jwt"
	TypShadownetCredJWT = "shadownet-cred+jwt"
	TypShadownetCSRJWT  = "shadownet-csr+jwt"
	TypJOSE             = "JOSE" // A2A §8.4.2 AgentCardSignature protected header
)

// Content types.
const (
	MediaA2AJSON     = "application/a2a+json"
	MediaJOSE        = "application/jose"
	MediaProblemJSON = "application/problem+json"
	MediaTextPlain   = "text/plain"
)

// HTTP headers Shadownet cares about (mixed-case-with-dashes per RFC 0001 §2).
const (
	HeaderA2AExtensions = "A2A-Extensions"
	HeaderA2AVersion    = "A2A-Version"
)

// Identifiers in URN form.
const (
	// ExtensionURI is the A2A extension URI that Shadownet ships under.
	ExtensionURI = "urn:shadownet:0.2"

	// ProtocolVersion is the literal v that appears in envelopes, DNS TXT
	// records, and AgentCards.
	ProtocolVersion = "0.2"

	// ErrorURNPrefix is the namespace for Shadownet RFC 7807 problem types.
	// Concrete codes: parse_error, signature, creds_required, creds_rejected,
	// policy, replay, unknown_recipient, rate_limited (RFC 0001 §8.8).
	ErrorURNPrefix = "urn:shadownet:error:"
)

// AgentCard Shadownet extension field names.
const (
	FieldShadownetV              = "shadownet:v"
	FieldShadownetPK             = "shadownet:pk"
	FieldShadownetIssueEndpoint  = "shadownet:issueEndpoint"
	FieldShadownetStatusListBase = "shadownet:statusListBase"
	SecuritySchemePinnedSelfSign = "shadownet:pinned-self-signed"
)
