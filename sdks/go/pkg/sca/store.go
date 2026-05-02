// SPDX-License-Identifier: MIT

package sca

import (
	"context"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

// SessionStore persists proof sessions through their RFC-0004 lifecycle.
//
// Implementations live outside `pkg/sca`: in-memory and SQLite under
// `internal/store{mem,sqlite}`; operators write their own for other backends.
type SessionStore interface {
	// Put inserts a new session in pending state.
	Put(ctx context.Context, s Session) error

	// Get returns a session by ID. Returns ErrSessionNotFound when absent.
	Get(ctx context.Context, id string) (Session, error)

	// MarkReady atomically transitions a pending session to ready and stamps
	// ReadyAt + ExpiresAt. Returns ErrSessionState if not in pending state.
	MarkReady(ctx context.Context, id string, at time.Time) error

	// Consume atomically transitions a ready session to consumed. Returns
	// ErrSessionState if not in ready state.
	Consume(ctx context.Context, id string) error

	// Fail transitions a session to failed (terminal).
	Fail(ctx context.Context, id string) error
}

// IssuedCredential is the SCA-side record kept for every credential issued.
//
// The JWT field carries the wire form so /freshness can re-attest a known
// jti without re-issuing the credential, and so audit log dumps can produce
// the original artifact.
type IssuedCredential struct {
	JTI             string
	Issuer          string
	Subject         string
	Level           string
	SubjectType     vc.SubjectType
	JWT             string
	StatusListID    string
	StatusListIndex uint64
	IssuedAt        time.Time
	Expires         time.Time
}

// IssuanceStore records and retrieves credentials by jti.
type IssuanceStore interface {
	Put(ctx context.Context, c IssuedCredential) error
	Get(ctx context.Context, jti string) (IssuedCredential, error)
}

// RevocationStore manages the SCA's BitstringStatusList(s).
//
// At v0.1 a single active list is enough; AssignIndex returns its ID and the
// next free index. Operators that want sharded lists (e.g. one per quarter)
// implement their own RevocationStore that rotates listID over time.
type RevocationStore interface {
	// AssignIndex hands out the next free (listID, index) pair.
	AssignIndex(ctx context.Context) (listID string, index uint64, err error)

	// Revoke sets the revocation bit at (listID, index).
	Revoke(ctx context.Context, listID string, index uint64) error

	// Snapshot returns the current bitstring for listID.
	Snapshot(ctx context.Context, listID string) (*vc.StatusList, error)
}
