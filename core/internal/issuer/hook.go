// SPDX-License-Identifier: MIT

package issuer

import (
	"context"
	"crypto/ed25519"
	"time"
)

// Outcome is the ceremony-hook decision.
type Outcome int

const (
	// OutcomeApprove signals the Issuer SHOULD mint the credential
	// immediately and return it with HTTP 200.
	OutcomeApprove Outcome = iota

	// OutcomePending signals the Subject MUST complete an out-of-band
	// ceremony at NextURL and re-POST. The HTTP response is
	// 409 ceremony_pending with { "next": <NextURL> }.
	OutcomePending

	// OutcomeReject is terminal. Returns 403 ceremony_failed with Reason
	// in the problem+json body.
	OutcomeReject
)

// String returns the wire-form outcome name (used in admin tooling and logs).
func (o Outcome) String() string {
	switch o {
	case OutcomeApprove:
		return "approve"
	case OutcomePending:
		return "pending"
	case OutcomeReject:
		return "reject"
	default:
		return "unknown"
	}
}

// Decision is what a Hook returns. Approve + NextURL are used per
// outcome; HandleID and CeremonyExpiry are used when the Outcome is
// Pending so the Store can park the ceremony idempotently.
type Decision struct {
	Outcome        Outcome
	Reason         string    // surfaced on 403
	NextURL        string    // required for OutcomePending
	HandleID       string    // opaque per-ceremony key (set by hook; defaults to idempotency key)
	CeremonyExpiry time.Time // when the parked ceremony becomes ErrCeremonyDead
}

// CSRView is the read-only projection of an inbound CSR the Hook sees.
// Decoupled from internal/csr.Payload so hooks can be tested without
// pulling JWT plumbing in.
type CSRView struct {
	Iss      string // CSR Subject identifier (Shadowname or pubkey)
	Aud      string // Issuer's own identifier
	Kind     string
	Org      string
	IssuedAt time.Time
	Expiry   time.Time // CSR's own exp — bounds the ceremony lifetime
}

// Hook decides what to do with an incoming CSR. Implementations:
//
//   - hooks/dev.AutoApprove — auto-mints; gated by SHADOWNET_ALLOW_AUTO_APPROVE
//     on non-loopback listeners. Suitable only for development.
//   - hooks/queue.Queue — parks the ceremony in the Store and surfaces
//     `next` URL pointing back at the same Issuer for the re-POST after
//     admin approval. Production default for org_affiliation ceremonies
//     that need a human in the loop.
//   - hooks/webhook.HMAC — forwards the CSR to an operator-controlled
//     HTTP endpoint and reads the Decision back.
//
// Evaluate runs synchronously on the HTTP-handler goroutine. Long
// operations MUST respect ctx and return promptly; the queue + webhook
// hooks parallel-process at the storage layer rather than blocking the
// request.
type Hook interface {
	Evaluate(ctx context.Context, csr CSRView, subjectPub ed25519.PublicKey) (Decision, error)
}
