// SPDX-License-Identifier: MIT

package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/sns"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

// SNSRecordStore implements sns.RecordStore against Postgres. Local keys are
// canonicalized to lowercase to match the wire grammar (case-insensitive).
type SNSRecordStore struct{ pool *pgxpool.Pool }

// NewSNSRecordStore returns a RecordStore backed by pool.
func NewSNSRecordStore(pool *pgxpool.Pool) *SNSRecordStore { return &SNSRecordStore{pool: pool} }

// Get implements sns.RecordStore.
func (s *SNSRecordStore) Get(ctx context.Context, local string) (sns.Record, error) {
	var (
		rec       sns.Record
		pubJSON   []byte
		subjType  string
		tombstone bool
	)
	err := s.pool.QueryRow(ctx, `
SELECT shadowname, did, endpoint, public_key, subject_type, ttl, issued_at, tombstone
FROM   sns_records WHERE local = $1`, strings.ToLower(local)).Scan(
		&rec.Shadowname, &rec.DID, &rec.Endpoint, &pubJSON, &subjType, &rec.TTL, &rec.IssuedAt, &tombstone,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return sns.Record{}, sns.ErrRecordNotFound
	}
	if err != nil {
		return sns.Record{}, fmt.Errorf("pgstore: get sns record: %w", err)
	}
	if tombstone {
		return sns.Record{}, sns.ErrRecordTombstoned
	}
	var jwk crypto.JWK
	if err := json.Unmarshal(pubJSON, &jwk); err != nil {
		return sns.Record{}, fmt.Errorf("pgstore: decode publicKey: %w", err)
	}
	rec.PublicKey = jwk
	rec.SubjectType = vc.SubjectType(subjType)
	rec.IssuedAt = rec.IssuedAt.UTC()
	return rec, nil
}

// Put implements sns.RecordStore. UPSERT semantics; clears tombstone on
// re-registration.
func (s *SNSRecordStore) Put(ctx context.Context, r sns.Record) error {
	parts := strings.SplitN(r.Shadowname, "@", 2)
	if len(parts) != 2 {
		return fmt.Errorf("pgstore: shadowname %q lacks '@'", r.Shadowname)
	}
	local := strings.ToLower(parts[0])
	pubJSON, err := json.Marshal(r.PublicKey)
	if err != nil {
		return fmt.Errorf("pgstore: encode publicKey: %w", err)
	}
	_, err = s.pool.Exec(
		ctx, `
INSERT INTO sns_records
  (local, shadowname, did, endpoint, public_key, subject_type, ttl, issued_at, tombstone)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, FALSE)
ON CONFLICT (local) DO UPDATE SET
  shadowname   = EXCLUDED.shadowname,
  did          = EXCLUDED.did,
  endpoint     = EXCLUDED.endpoint,
  public_key   = EXCLUDED.public_key,
  subject_type = EXCLUDED.subject_type,
  ttl          = EXCLUDED.ttl,
  issued_at    = EXCLUDED.issued_at,
  tombstone    = FALSE`,
		local, r.Shadowname, r.DID, r.Endpoint, pubJSON, string(r.SubjectType), r.TTL, r.IssuedAt,
	)
	if err != nil {
		return fmt.Errorf("pgstore: put sns record: %w", err)
	}
	return nil
}

// Delete implements sns.RecordStore by inserting a tombstone row (or flipping
// the tombstone flag on an existing one).
func (s *SNSRecordStore) Delete(ctx context.Context, local string) error {
	_, err := s.pool.Exec(ctx, `
INSERT INTO sns_records
  (local, shadowname, did, endpoint, public_key, subject_type, ttl, issued_at, tombstone)
VALUES ($1, '', '', '', '{}'::jsonb, '', 0, NOW(), TRUE)
ON CONFLICT (local) DO UPDATE SET tombstone = TRUE`, strings.ToLower(local))
	if err != nil {
		return fmt.Errorf("pgstore: delete sns record: %w", err)
	}
	return nil
}
