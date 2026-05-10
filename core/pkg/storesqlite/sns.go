// SPDX-License-Identifier: MIT

package storesqlite

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/sns"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

const snsSchema = `
CREATE TABLE IF NOT EXISTS sns_records (
  local         TEXT PRIMARY KEY COLLATE NOCASE,
  shadowname    TEXT NOT NULL,
  did           TEXT NOT NULL,
  endpoint      TEXT NOT NULL,
  public_key    TEXT NOT NULL,
  subject_type  TEXT NOT NULL,
  ttl           INTEGER NOT NULL,
  issued_at     INTEGER NOT NULL,
  tombstone     INTEGER NOT NULL DEFAULT 0
);
`

// OpenSNS returns a *sql.DB connected to dsn with the SNS schema applied.
//
// The database may already have the SCA schema (Open above); applying both is
// idempotent.
func OpenSNS(dsn string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("storesqlite: open: %w", err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(snsSchema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("storesqlite: apply sns schema: %w", err)
	}
	return db, nil
}

// SNSRecordStore implements sns.RecordStore against SQLite.
type SNSRecordStore struct{ db *sql.DB }

// NewSNSRecordStore returns a RecordStore backed by db.
func NewSNSRecordStore(db *sql.DB) *SNSRecordStore { return &SNSRecordStore{db: db} }

// Get implements sns.RecordStore.
func (s *SNSRecordStore) Get(ctx context.Context, local string) (sns.Record, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT shadowname, did, endpoint, public_key, subject_type, ttl, issued_at, tombstone
FROM sns_records WHERE local = ?`, strings.ToLower(local))
	var (
		rec       sns.Record
		pubJSON   string
		subjType  string
		issuedAt  int64
		tombstone int
	)
	if err := row.Scan(&rec.Shadowname, &rec.DID, &rec.Endpoint, &pubJSON, &subjType, &rec.TTL, &issuedAt, &tombstone); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return sns.Record{}, sns.ErrRecordNotFound
		}
		return sns.Record{}, fmt.Errorf("storesqlite: get sns record: %w", err)
	}
	if tombstone == 1 {
		return sns.Record{}, sns.ErrRecordTombstoned
	}
	var jwk crypto.JWK
	if err := json.Unmarshal([]byte(pubJSON), &jwk); err != nil {
		return sns.Record{}, fmt.Errorf("storesqlite: decode publicKey: %w", err)
	}
	rec.PublicKey = jwk
	rec.SubjectType = vc.SubjectType(subjType)
	rec.IssuedAt = time.Unix(issuedAt, 0).UTC()
	return rec, nil
}

// Put implements sns.RecordStore.
func (s *SNSRecordStore) Put(ctx context.Context, r sns.Record) error {
	parts := strings.SplitN(r.Shadowname, "@", 2)
	if len(parts) != 2 {
		return fmt.Errorf("storesqlite: shadowname %q lacks '@'", r.Shadowname)
	}
	local := strings.ToLower(parts[0])
	pubJSON, err := json.Marshal(r.PublicKey)
	if err != nil {
		return fmt.Errorf("storesqlite: encode publicKey: %w", err)
	}
	_, err = s.db.ExecContext(ctx, `
INSERT INTO sns_records (local, shadowname, did, endpoint, public_key, subject_type, ttl, issued_at, tombstone)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
ON CONFLICT(local) DO UPDATE SET
  shadowname=excluded.shadowname,
  did=excluded.did,
  endpoint=excluded.endpoint,
  public_key=excluded.public_key,
  subject_type=excluded.subject_type,
  ttl=excluded.ttl,
  issued_at=excluded.issued_at,
  tombstone=0`,
		local, r.Shadowname, r.DID, r.Endpoint, string(pubJSON), string(r.SubjectType), r.TTL, r.IssuedAt.Unix())
	if err != nil {
		return fmt.Errorf("storesqlite: put sns record: %w", err)
	}
	return nil
}

// Delete implements sns.RecordStore.
func (s *SNSRecordStore) Delete(ctx context.Context, local string) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO sns_records (local, shadowname, did, endpoint, public_key, subject_type, ttl, issued_at, tombstone)
VALUES (?, '', '', '', '{}', '', 0, 0, 1)
ON CONFLICT(local) DO UPDATE SET tombstone=1`, strings.ToLower(local))
	if err != nil {
		return fmt.Errorf("storesqlite: delete sns record: %w", err)
	}
	return nil
}
