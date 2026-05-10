// SPDX-License-Identifier: MIT

package sns

import (
	"context"
	"errors"
)

// Sentinel errors a RecordStore may return.
var (
	ErrRecordNotFound   = errors.New("sns: record not found")
	ErrRecordTombstoned = errors.New("sns: record tombstoned")
)

// RecordStore persists Records keyed by canonical lower-case `local`. It is
// the SNS provider's source of truth; the SignedRecord JWT is freshly minted
// from this state on every resolve so signatures bind iat/exp to wall time.
type RecordStore interface {
	// Get returns the record for local. Returns ErrRecordNotFound when no row
	// exists, ErrRecordTombstoned when the row was deleted.
	Get(ctx context.Context, local string) (Record, error)

	// Put inserts or replaces the record for r.Shadowname's local part.
	Put(ctx context.Context, r Record) error

	// Delete tombstones the record for local.
	Delete(ctx context.Context, local string) error
}
