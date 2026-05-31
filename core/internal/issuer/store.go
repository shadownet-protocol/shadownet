// SPDX-License-Identifier: MIT

package issuer

import (
	"context"
	"time"
)

// Store is the persistence interface the Issuer HTTP server consumes.
// Default implementation lives in internal/issuer/sqlitestore; production
// deployments can swap in pgstore by satisfying this interface.
//
// All write methods MUST be safe for concurrent invocation. AllocateIndex
// in particular is the hot path during issuance and MUST serialize
// updates of (epoch.next_idx) so two concurrent CSRs can't be issued the
// same revocation idx.
type Store interface {
	// Credentials.
	PutCredential(ctx context.Context, c Credential) error
	GetByIdempotencyKey(ctx context.Context, key string) (Credential, error)

	// Pending ceremonies.
	PutPending(ctx context.Context, p Pending) error
	GetPending(ctx context.Context, handleID string) (Pending, error)
	GetPendingByIdempotencyKey(ctx context.Context, key string) (Pending, error)
	ListPending(ctx context.Context, filter PendingFilter) ([]Pending, error)
	UpdatePendingStatus(ctx context.Context, handleID string, status PendingStatus, reason string, at time.Time) error

	// Status epochs.
	CurrentEpoch(ctx context.Context) (Epoch, error)
	GetEpoch(ctx context.Context, n uint64) (Epoch, error)
	ListOpenEpochs(ctx context.Context) ([]Epoch, error)
	RotateEpoch(ctx context.Context, maxIndices uint64, at time.Time) (Epoch, error)
	AllocateIndex(ctx context.Context, credentialExp time.Time) (epoch, idx uint64, err error)
	SetRevoked(ctx context.Context, epoch, idx uint64, at time.Time) error

	// Status-bit rendering. The Store returns the raw bitstring bytes for
	// the named epoch plus the wall-clock time of the latest revocation
	// recorded (used to set Cache-Control freshness hints upstream).
	LoadStatusBits(ctx context.Context, epoch uint64) ([]byte, time.Time, error)

	// Lifecycle.
	Ping(ctx context.Context) error
	Close() error
}
