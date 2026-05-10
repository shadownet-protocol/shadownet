// SPDX-License-Identifier: MIT

package sca

import (
	"context"
	"time"
)

// SessionState is the lifecycle of a proof session per RFC-0004 §Session lifecycle.
type SessionState string

// Session lifecycle states.
const (
	StatePending  SessionState = "pending"
	StateReady    SessionState = "ready"
	StateConsumed SessionState = "consumed"
	StateFailed   SessionState = "failed"
	StateExpired  SessionState = "expired"
)

// PendingTTL is the lifetime of a pending session per RFC-0004.
const PendingTTL = time.Hour

// ReadyTTL is the lifetime of a ready session per RFC-0004.
const ReadyTTL = time.Hour

// NextStep mirrors the `next` field returned by /proof/start.
type NextStep struct {
	Kind string `json:"kind"`          // redirect | embed | email-link | in-person
	URL  string `json:"url,omitempty"` // present for redirect/embed
	TTL  int    `json:"ttl,omitempty"` // seconds
}

// NextStep kinds defined in RFC-0004.
const (
	StepRedirect  = "redirect"
	StepEmbed     = "embed"
	StepEmailLink = "email-link"
	StepInPerson  = "in-person"
)

// Session is a proof session opened by /proof/start.
type Session struct {
	ID          string
	Subject     string // subject DID
	Level       string
	Method      string
	State       SessionState
	Next        NextStep
	CallbackURL string
	CreatedAt   time.Time
	ReadyAt     time.Time // zero until State == ready
	ExpiresAt   time.Time
}

// ProofMethod is the plug-point that adapts an out-of-protocol proof flow
// (email verification, government-ID document check, biometric kiosk, …) to
// Shadownet's session model.
//
// `pkg/sca` ships zero implementations. `cmd/sca-server` ships exactly one,
// `InstantApprovalProofMethod`, for local dev. Operators that need other
// methods write them in their own deployment, satisfying this interface.
type ProofMethod interface {
	// Name returns the method identifier; matches the `method` value an SCA
	// declares in its policy document and returns in /proof/start.
	Name() string

	// Start kicks off the proof flow for sess. The implementation populates
	// the NextStep the client should follow. If the method is synchronous
	// (e.g. instant-approval), it MAY return a non-nil readyAt to indicate
	// the session is immediately ready; the Issuer will atomically persist
	// that transition.
	Start(ctx context.Context, sess Session) (next NextStep, readyAt *time.Time, err error)
}
