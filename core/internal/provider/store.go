// SPDX-License-Identifier: MIT

package provider

import "context"

// Store is the persistence interface the Provider HTTP server consumes.
// The default in-process implementation lives in
// internal/provider/sqlitestore; production deployments can swap in
// core/pgstore by satisfying this interface.
type Store interface {
	// GetRecord returns the Record for local, or ErrNotFound if no such
	// Shadowname is registered with this Provider.
	GetRecord(ctx context.Context, local string) (Record, error)

	// PutRecord inserts or updates a Record (keyed by Record.Local).
	PutRecord(ctx context.Context, r Record) error

	// DeleteRecord removes a Record; deleting a missing record is not an
	// error.
	DeleteRecord(ctx context.Context, local string) error

	// ListRecords returns all registered records. Used by admin tooling.
	ListRecords(ctx context.Context) ([]Record, error)

	// Ping returns nil when the store is reachable. Used as the /readyz
	// hook in the server.
	Ping(ctx context.Context) error

	// Close releases any underlying connection. Called from server
	// shutdown.
	Close() error
}
