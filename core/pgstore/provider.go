// SPDX-License-Identifier: MIT

package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/shadownet-protocol/shadownet/core/internal/provider"
)

// ProviderStore implements internal/provider.Store on top of Postgres.
type ProviderStore struct {
	pool *pgxpool.Pool
}

// NewProviderStore returns a ProviderStore using the given pool.
func NewProviderStore(pool *pgxpool.Pool) *ProviderStore {
	return &ProviderStore{pool: pool}
}

// Close is a no-op — the pool is owned by the caller.
func (s *ProviderStore) Close() error { return nil }

// Ping forwards to the underlying pool.
func (s *ProviderStore) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

// GetRecord returns the Record keyed on `local` or provider.ErrNotFound.
func (s *ProviderStore) GetRecord(ctx context.Context, local string) (provider.Record, error) {
	var r provider.Record
	err := s.pool.QueryRow(
		ctx,
		`SELECT local, shadow_pk, a2a_url, display_name, description, version, created_at, updated_at
		   FROM provider_records WHERE local = $1`, local,
	).Scan(&r.Local, &r.ShadowPublicKey, &r.A2AURL, &r.DisplayName, &r.Description, &r.Version, &r.CreatedAt, &r.UpdatedAt)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		return provider.Record{}, provider.ErrNotFound
	case err != nil:
		return provider.Record{}, fmt.Errorf("pgstore: select provider record: %w", err)
	}
	return r, nil
}

// PutRecord inserts or updates a Record. CreatedAt is set on first insert
// only; UpdatedAt advances on every write.
func (s *ProviderStore) PutRecord(ctx context.Context, r provider.Record) error {
	now := time.Now().UTC()
	if r.CreatedAt.IsZero() {
		r.CreatedAt = now
	}
	if r.Version == "" {
		r.Version = "1.0.0"
	}
	_, err := s.pool.Exec(
		ctx,
		`INSERT INTO provider_records
		    (local, shadow_pk, a2a_url, display_name, description, version, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		 ON CONFLICT (local) DO UPDATE SET
		    shadow_pk    = EXCLUDED.shadow_pk,
		    a2a_url      = EXCLUDED.a2a_url,
		    display_name = EXCLUDED.display_name,
		    description  = EXCLUDED.description,
		    version      = EXCLUDED.version,
		    updated_at   = EXCLUDED.updated_at`,
		r.Local, r.ShadowPublicKey, r.A2AURL, r.DisplayName, r.Description, r.Version,
		r.CreatedAt, now,
	)
	if err != nil {
		return fmt.Errorf("pgstore: upsert provider record: %w", err)
	}
	return nil
}

// DeleteRecord removes a Record; deleting a missing record is not an error.
func (s *ProviderStore) DeleteRecord(ctx context.Context, local string) error {
	if _, err := s.pool.Exec(ctx, `DELETE FROM provider_records WHERE local = $1`, local); err != nil {
		return fmt.Errorf("pgstore: delete provider record: %w", err)
	}
	return nil
}

// ListRecords returns every registered record ordered by local ascending.
func (s *ProviderStore) ListRecords(ctx context.Context) ([]provider.Record, error) {
	rows, err := s.pool.Query(ctx,
		`SELECT local, shadow_pk, a2a_url, display_name, description, version, created_at, updated_at
		   FROM provider_records ORDER BY local`)
	if err != nil {
		return nil, fmt.Errorf("pgstore: list provider records: %w", err)
	}
	defer rows.Close()
	var out []provider.Record
	for rows.Next() {
		var r provider.Record
		if err := rows.Scan(&r.Local, &r.ShadowPublicKey, &r.A2AURL, &r.DisplayName, &r.Description, &r.Version, &r.CreatedAt, &r.UpdatedAt); err != nil {
			return nil, fmt.Errorf("pgstore: scan provider row: %w", err)
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// Ensure ProviderStore satisfies provider.Store at compile time.
var _ provider.Store = (*ProviderStore)(nil)
