// SPDX-License-Identifier: MIT

// Package issuer hosts the Shadownet Issuer HTTP service — issuing
// org_affiliation credentials (RFC 0001 §6.5) and serving per-epoch
// revocation bitstrings (§6.4). One Issuer instance can operate in either
// domain mode (well-known paths under /.well-known/shadownet/) or
// keyed-Hub mode (self-served AgentCard plus paths declared via
// shadownet:issueEndpoint + shadownet:statusListBase).
package issuer

import (
	"errors"
	"time"
)

// Mode picks the URL convention the server runs under.
type Mode int

const (
	// ModeDomain is the Shadowname-style Issuer: routes live at the
	// canonical well-known paths
	// (/.well-known/shadownet/issue, /.well-known/shadownet/status/<epoch>).
	// The Issuer is addressed by its DNS domain.
	ModeDomain Mode = iota

	// ModeKeyed is the keyed-Hub Issuer: routes live at operator-chosen
	// paths declared via shadownet:issueEndpoint and shadownet:statusListBase
	// on a self-served AgentCard at /.well-known/agent-card.json. The
	// Issuer is addressed by its multibase Ed25519 public key.
	ModeKeyed
)

// String returns a human-readable mode name (for diagnostics).
func (m Mode) String() string {
	switch m {
	case ModeDomain:
		return "domain"
	case ModeKeyed:
		return "keyed"
	default:
		return "unknown"
	}
}

// PendingStatus is the lifecycle state of a ceremony.
type PendingStatus int

const (
	// PendingNew is the initial state when a ceremony is first parked.
	PendingNew PendingStatus = iota
	// PendingApproved means an operator (or a hook) has signed off; the
	// next CSR re-POST will mint the credential.
	PendingApproved
	// PendingRejected is terminal: the next CSR re-POST returns
	// 403 ceremony_failed with the recorded reason.
	PendingRejected
)

// String renders the PendingStatus name (also the wire form admin tooling
// surfaces to operators).
func (s PendingStatus) String() string {
	switch s {
	case PendingNew:
		return "new"
	case PendingApproved:
		return "approved"
	case PendingRejected:
		return "rejected"
	default:
		return "unknown"
	}
}

// PendingFilter narrows ListPending queries.
type PendingFilter struct {
	// Status, when non-nil, restricts results to that status.
	Status *PendingStatus
	// IncludeExpired controls whether ceremonies whose CSR `exp` has
	// passed are surfaced. Default false (only live pendings).
	IncludeExpired bool
	// Limit caps the result size. 0 → store default.
	Limit int
}

// Credential is the persisted record of an issued shadownet-cred+jwt.
// The JWS field carries the verbatim compact JWT bytes so idempotent
// re-POSTs return byte-identical tokens (per RFC 0001 §6.5 "issuers SHOULD
// treat repeated CSRs as idempotent within the lifetime of one ceremony").
type Credential struct {
	IdempotencyKey string
	JWS            string
	Iss            string
	Sub            string
	Org            string
	Epoch          uint64
	Idx            uint64
	IssuedAt       time.Time
	ExpiresAt      time.Time
}

// Pending is the persisted record of a parked ceremony. A new CSR landing
// at the Issuer creates one with status PendingNew; the admin CLI (or a
// hook) advances it to PendingApproved or PendingRejected.
type Pending struct {
	HandleID       string
	IdempotencyKey string
	Iss            string // CSR Subject identifier (Shadowname or pubkey)
	Aud            string // Issuer identifier (domain or pubkey)
	Kind           string
	Org            string
	SubjectPubKey  string // multibase Ed25519 form (z6Mk…)
	Status         PendingStatus
	NextURL        string // surfaced in 409 ceremony_pending response
	Reason         string // surfaced in 403 ceremony_failed when rejected
	CreatedAt      time.Time
	UpdatedAt      time.Time
	CeremonyExpiry time.Time // derived from CSR exp; ceremony is dead after this
}

// Epoch is the metadata for one revocation epoch. Idx allocation runs
// against the open epoch; closed epochs are kept served until every
// credential they cover has expired (RFC 0001 §6.4).
type Epoch struct {
	Number              uint64
	MaxIndices          uint64
	NextIdx             uint64
	OpenedAt            time.Time
	ClosedAt            time.Time // zero value while open
	LastIssuedExpiresAt time.Time // tracks the latest exp under this epoch (for safe GC)
}

// IsOpen reports whether the epoch is still accepting new index
// allocations.
func (e Epoch) IsOpen() bool {
	return e.ClosedAt.IsZero()
}

// Sentinel errors. Callers MAY wrap with their own context.
var (
	ErrInvalid      = errors.New("issuer: invalid")
	ErrNotFound     = errors.New("issuer: not found")
	ErrCeremonyDead = errors.New("issuer: ceremony expired")
)
