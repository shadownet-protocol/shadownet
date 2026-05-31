// SPDX-License-Identifier: MIT

// Package provider hosts the Shadownet Provider HTTP service — serving
// signed A2A AgentCards at <ep>/identity/<local> per RFC 0001 §5.2. One
// Provider instance hosts many Shadownames (multi-tenant); each
// Shadowname's per-Shadow data is a Record in the store.
//
// Direct-mode Shadows do not flow through this server — they self-serve
// at /.well-known/agent-card.json on their own endpoint.
package provider

import (
	"errors"
	"time"
)

// ErrNotFound is the canonical store-miss error.
var ErrNotFound = errors.New("provider: shadowname not found")

// Record is the per-Shadow data the Provider needs to sign and serve an
// AgentCard for a single Shadowname.
type Record struct {
	// Local is the Shadowname's local part (canonical lowercase). The full
	// Shadowname is Local + "@" + Provider's configured Domain.
	Local string

	// ShadowPublicKey is the multibase Ed25519 form (z6Mk…) of the Shadow's
	// signing key — this is what gets baked into the signed AgentCard as
	// shadownet:pk and what envelope receivers verify against.
	ShadowPublicKey string

	// A2AURL is the receiver-side A2A endpoint senders POST message:send
	// to. Surfaces as supportedInterfaces[0].url on the AgentCard.
	A2AURL string

	// DisplayName is the human-readable name shown on the card. Defaults
	// to Local if empty.
	DisplayName string

	// Description is the human-readable card description. Defaults to
	// "<Local>@<Domain>" if empty.
	Description string

	// Version is the per-Shadow build version surfaced in the card.
	Version string

	// CreatedAt records when the record was first registered. Used by
	// admin tooling, not part of the wire shape.
	CreatedAt time.Time

	// UpdatedAt records the last mutation timestamp.
	UpdatedAt time.Time
}
