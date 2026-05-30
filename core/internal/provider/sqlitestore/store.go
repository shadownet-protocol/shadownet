// SPDX-License-Identifier: MIT

// Package sqlitestore is the default Provider Store implementation,
// backed by a single SQLite database file. Zero configuration; the
// schema is auto-applied on Open and migrations are forward-only.
package sqlitestore

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	// modernc.org/sqlite registers the "sqlite" sql.DB driver via its init();
	// this package is non-main so revive demands the justification.
	_ "modernc.org/sqlite"

	"github.com/shadownet-protocol/shadownet/core/internal/provider"
)

const schema = `
CREATE TABLE IF NOT EXISTS provider_records (
    local            TEXT PRIMARY KEY,
    shadow_pk        TEXT NOT NULL,
    a2a_url          TEXT NOT NULL,
    display_name     TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    version          TEXT NOT NULL DEFAULT '1.0.0',
    created_at_unix  INTEGER NOT NULL,
    updated_at_unix  INTEGER NOT NULL
);
`

// Store is the SQLite-backed implementation of provider.Store.
type Store struct {
	db *sql.DB
}

// Open opens (or creates) the SQLite database at dsn. The dsn is the
// modernc.org/sqlite DSN form: a filesystem path, or ":memory:" for an
// in-process database, or "file:<path>?<query>" for the full URI form.
func Open(dsn string) (*Store, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("sqlitestore: open: %w", err)
	}
	if err := db.PingContext(context.Background()); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: ping: %w", err)
	}
	if _, err := db.Exec(schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: apply schema: %w", err)
	}
	return &Store{db: db}, nil
}

// Close releases the underlying database handle.
func (s *Store) Close() error { return s.db.Close() }

// Ping returns nil when the underlying database is reachable.
func (s *Store) Ping(ctx context.Context) error { return s.db.PingContext(ctx) }

// GetRecord implements provider.Store.
func (s *Store) GetRecord(ctx context.Context, local string) (provider.Record, error) {
	var r provider.Record
	var createdUnix, updatedUnix int64
	err := s.db.QueryRowContext(
		ctx,
		`SELECT local, shadow_pk, a2a_url, display_name, description, version, created_at_unix, updated_at_unix
		   FROM provider_records WHERE local = ?`,
		local,
	).Scan(&r.Local, &r.ShadowPublicKey, &r.A2AURL, &r.DisplayName, &r.Description, &r.Version, &createdUnix, &updatedUnix)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return provider.Record{}, provider.ErrNotFound
	case err != nil:
		return provider.Record{}, fmt.Errorf("sqlitestore: select: %w", err)
	}
	r.CreatedAt = time.Unix(createdUnix, 0)
	r.UpdatedAt = time.Unix(updatedUnix, 0)
	return r, nil
}

// PutRecord implements provider.Store. Insert-or-update; CreatedAt is set
// once on first insert, UpdatedAt advances on every write.
func (s *Store) PutRecord(ctx context.Context, r provider.Record) error {
	now := time.Now().Unix()
	if r.CreatedAt.IsZero() {
		r.CreatedAt = time.Unix(now, 0)
	}
	if r.Version == "" {
		r.Version = "1.0.0"
	}
	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO provider_records
		    (local, shadow_pk, a2a_url, display_name, description, version, created_at_unix, updated_at_unix)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(local) DO UPDATE SET
		    shadow_pk = excluded.shadow_pk,
		    a2a_url = excluded.a2a_url,
		    display_name = excluded.display_name,
		    description = excluded.description,
		    version = excluded.version,
		    updated_at_unix = excluded.updated_at_unix`,
		r.Local, r.ShadowPublicKey, r.A2AURL, r.DisplayName, r.Description, r.Version,
		r.CreatedAt.Unix(), now,
	)
	if err != nil {
		return fmt.Errorf("sqlitestore: upsert: %w", err)
	}
	return nil
}

// DeleteRecord implements provider.Store. Deleting a missing record is a
// no-op.
func (s *Store) DeleteRecord(ctx context.Context, local string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM provider_records WHERE local = ?`, local)
	if err != nil {
		return fmt.Errorf("sqlitestore: delete: %w", err)
	}
	return nil
}

// ListRecords implements provider.Store.
func (s *Store) ListRecords(ctx context.Context) ([]provider.Record, error) {
	rows, err := s.db.QueryContext(
		ctx,
		`SELECT local, shadow_pk, a2a_url, display_name, description, version, created_at_unix, updated_at_unix
		   FROM provider_records ORDER BY local`,
	)
	if err != nil {
		return nil, fmt.Errorf("sqlitestore: list: %w", err)
	}
	defer rows.Close()
	var out []provider.Record
	for rows.Next() {
		var r provider.Record
		var createdUnix, updatedUnix int64
		if err := rows.Scan(&r.Local, &r.ShadowPublicKey, &r.A2AURL, &r.DisplayName, &r.Description, &r.Version, &createdUnix, &updatedUnix); err != nil {
			return nil, fmt.Errorf("sqlitestore: scan: %w", err)
		}
		r.CreatedAt = time.Unix(createdUnix, 0)
		r.UpdatedAt = time.Unix(updatedUnix, 0)
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("sqlitestore: rows: %w", err)
	}
	return out, nil
}
