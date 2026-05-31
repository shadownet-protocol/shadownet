// SPDX-License-Identifier: MIT

package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
)

// DefaultMaxIndices is the per-epoch ceiling new epochs are opened with
// when the caller passes 0 to NewIssuerStore. Matches the
// sqlitestore.DefaultMaxIndices value.
const DefaultMaxIndices uint64 = 131072

// IssuerStore implements internal/issuer.Store on top of Postgres.
type IssuerStore struct {
	pool       *pgxpool.Pool
	maxIndices uint64
}

// NewIssuerStore returns an IssuerStore using the given pool. maxIndices
// is the ceiling new epochs are opened with; pass 0 for DefaultMaxIndices.
// On first call the bootstrap epoch (number=1) is opened if no open epoch
// exists.
func NewIssuerStore(ctx context.Context, pool *pgxpool.Pool, maxIndices uint64) (*IssuerStore, error) {
	if maxIndices == 0 {
		maxIndices = DefaultMaxIndices
	}
	s := &IssuerStore{pool: pool, maxIndices: maxIndices}
	if err := s.ensureCurrentEpoch(ctx); err != nil {
		return nil, fmt.Errorf("pgstore: bootstrap epoch: %w", err)
	}
	return s, nil
}

// Close is a no-op; the pool is owned by the caller.
func (s *IssuerStore) Close() error { return nil }

// Ping forwards to the underlying pool.
func (s *IssuerStore) Ping(ctx context.Context) error { return s.pool.Ping(ctx) }

func (s *IssuerStore) ensureCurrentEpoch(ctx context.Context) error {
	var n int
	if err := s.pool.QueryRow(
		ctx,
		`SELECT COUNT(*) FROM issuer_epochs WHERE closed_at IS NULL`,
	).Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	_, err := s.pool.Exec(
		ctx,
		`INSERT INTO issuer_epochs (number, max_indices, next_idx, opened_at)
		 VALUES (1, $1, 0, NOW())`,
		int64(s.maxIndices),
	)
	return err
}

// PutCredential inserts an issued credential. Returns an error if the
// idempotency_key already exists — callers MUST do a GetByIdempotencyKey
// first to honor §6.5 idempotent re-POSTs.
func (s *IssuerStore) PutCredential(ctx context.Context, c issuer.Credential) error {
	_, err := s.pool.Exec(
		ctx,
		`INSERT INTO issuer_credentials
		    (idempotency_key, jws, iss, sub, org, epoch, idx, issued_at, expires_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
		c.IdempotencyKey, c.JWS, c.Iss, c.Sub, c.Org, int64(c.Epoch), int64(c.Idx),
		c.IssuedAt.UTC(), c.ExpiresAt.UTC(),
	)
	if err != nil {
		return fmt.Errorf("pgstore: insert credential: %w", err)
	}
	return nil
}

// GetByIdempotencyKey returns the credential previously issued under key,
// or issuer.ErrNotFound.
func (s *IssuerStore) GetByIdempotencyKey(ctx context.Context, key string) (issuer.Credential, error) {
	var c issuer.Credential
	var epoch, idx int64
	err := s.pool.QueryRow(
		ctx,
		`SELECT idempotency_key, jws, iss, sub, org, epoch, idx, issued_at, expires_at
		   FROM issuer_credentials WHERE idempotency_key = $1`, key,
	).Scan(&c.IdempotencyKey, &c.JWS, &c.Iss, &c.Sub, &c.Org, &epoch, &idx, &c.IssuedAt, &c.ExpiresAt)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		return issuer.Credential{}, issuer.ErrNotFound
	case err != nil:
		return issuer.Credential{}, fmt.Errorf("pgstore: select credential: %w", err)
	}
	c.Epoch = uint64(epoch)
	c.Idx = uint64(idx)
	return c, nil
}

// PutPending inserts or upserts a parked ceremony row.
func (s *IssuerStore) PutPending(ctx context.Context, p issuer.Pending) error {
	now := time.Now().UTC()
	if p.CreatedAt.IsZero() {
		p.CreatedAt = now
	}
	if p.UpdatedAt.IsZero() {
		p.UpdatedAt = now
	}
	_, err := s.pool.Exec(
		ctx,
		`INSERT INTO issuer_pendings
		    (handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
		     next_url, reason, created_at, updated_at, ceremony_expiry)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
		 ON CONFLICT (handle_id) DO UPDATE SET
		    status     = EXCLUDED.status,
		    next_url   = EXCLUDED.next_url,
		    reason     = EXCLUDED.reason,
		    updated_at = EXCLUDED.updated_at`,
		p.HandleID, p.IdempotencyKey, p.Iss, p.Aud, p.Kind, p.Org, p.SubjectPubKey, int16(p.Status),
		p.NextURL, p.Reason, p.CreatedAt.UTC(), p.UpdatedAt.UTC(), p.CeremonyExpiry.UTC(),
	)
	if err != nil {
		return fmt.Errorf("pgstore: upsert pending: %w", err)
	}
	return nil
}

// GetPending returns the Pending keyed on handleID.
func (s *IssuerStore) GetPending(ctx context.Context, handleID string) (issuer.Pending, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
		        next_url, reason, created_at, updated_at, ceremony_expiry
		   FROM issuer_pendings WHERE handle_id = $1`, handleID)
	return scanPendingRow(row)
}

// GetPendingByIdempotencyKey is the idempotent-lookup variant used by the
// HTTP handler to detect a re-POST.
func (s *IssuerStore) GetPendingByIdempotencyKey(ctx context.Context, key string) (issuer.Pending, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
		        next_url, reason, created_at, updated_at, ceremony_expiry
		   FROM issuer_pendings WHERE idempotency_key = $1`, key)
	return scanPendingRow(row)
}

func scanPendingRow(row pgx.Row) (issuer.Pending, error) {
	var p issuer.Pending
	var status int16
	if err := row.Scan(
		&p.HandleID, &p.IdempotencyKey, &p.Iss, &p.Aud, &p.Kind, &p.Org,
		&p.SubjectPubKey, &status, &p.NextURL, &p.Reason, &p.CreatedAt, &p.UpdatedAt, &p.CeremonyExpiry,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return issuer.Pending{}, issuer.ErrNotFound
		}
		return issuer.Pending{}, fmt.Errorf("pgstore: scan pending: %w", err)
	}
	p.Status = issuer.PendingStatus(status)
	return p, nil
}

// ListPending returns Pendings matching the filter. Default order is
// created_at ascending (oldest first).
func (s *IssuerStore) ListPending(ctx context.Context, filter issuer.PendingFilter) ([]issuer.Pending, error) {
	q := `SELECT handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
	             next_url, reason, created_at, updated_at, ceremony_expiry
	        FROM issuer_pendings WHERE TRUE`
	args := make([]any, 0, 3)
	if filter.Status != nil {
		args = append(args, int16(*filter.Status))
		q += fmt.Sprintf(" AND status = $%d", len(args))
	}
	if !filter.IncludeExpired {
		args = append(args, time.Now().UTC())
		q += fmt.Sprintf(" AND ceremony_expiry > $%d", len(args))
	}
	q += " ORDER BY created_at ASC"
	if filter.Limit > 0 {
		args = append(args, filter.Limit)
		q += fmt.Sprintf(" LIMIT $%d", len(args))
	}
	rows, err := s.pool.Query(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("pgstore: list pending: %w", err)
	}
	defer rows.Close()
	var out []issuer.Pending
	for rows.Next() {
		var p issuer.Pending
		var status int16
		if err := rows.Scan(
			&p.HandleID, &p.IdempotencyKey, &p.Iss, &p.Aud, &p.Kind, &p.Org,
			&p.SubjectPubKey, &status, &p.NextURL, &p.Reason, &p.CreatedAt, &p.UpdatedAt, &p.CeremonyExpiry,
		); err != nil {
			return nil, fmt.Errorf("pgstore: scan pending row: %w", err)
		}
		p.Status = issuer.PendingStatus(status)
		out = append(out, p)
	}
	return out, rows.Err()
}

// UpdatePendingStatus advances a ceremony's lifecycle.
func (s *IssuerStore) UpdatePendingStatus(ctx context.Context, handleID string, status issuer.PendingStatus, reason string, at time.Time) error {
	if at.IsZero() {
		at = time.Now()
	}
	tag, err := s.pool.Exec(
		ctx,
		`UPDATE issuer_pendings SET status = $1, reason = $2, updated_at = $3 WHERE handle_id = $4`,
		int16(status), reason, at.UTC(), handleID,
	)
	if err != nil {
		return fmt.Errorf("pgstore: update pending: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return issuer.ErrNotFound
	}
	return nil
}

// CurrentEpoch returns the single open epoch.
func (s *IssuerStore) CurrentEpoch(ctx context.Context) (issuer.Epoch, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT number, max_indices, next_idx, opened_at, closed_at, last_issued_expires_at
		   FROM issuer_epochs WHERE closed_at IS NULL
		   ORDER BY number DESC LIMIT 1`)
	return scanEpochRow(row)
}

// GetEpoch returns the epoch identified by number.
func (s *IssuerStore) GetEpoch(ctx context.Context, n uint64) (issuer.Epoch, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT number, max_indices, next_idx, opened_at, closed_at, last_issued_expires_at
		   FROM issuer_epochs WHERE number = $1`, int64(n))
	return scanEpochRow(row)
}

// ListOpenEpochs returns every open epoch (typically one).
func (s *IssuerStore) ListOpenEpochs(ctx context.Context) ([]issuer.Epoch, error) {
	rows, err := s.pool.Query(ctx,
		`SELECT number, max_indices, next_idx, opened_at, closed_at, last_issued_expires_at
		   FROM issuer_epochs WHERE closed_at IS NULL ORDER BY number ASC`)
	if err != nil {
		return nil, fmt.Errorf("pgstore: list epochs: %w", err)
	}
	defer rows.Close()
	var out []issuer.Epoch
	for rows.Next() {
		e, err := scanEpochRowCols(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func scanEpochRow(row pgx.Row) (issuer.Epoch, error) {
	var (
		e               issuer.Epoch
		num             int64
		maxIdx, nextIdx int64
		closed          *time.Time
		lastIssued      *time.Time
	)
	if err := row.Scan(&num, &maxIdx, &nextIdx, &e.OpenedAt, &closed, &lastIssued); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return issuer.Epoch{}, issuer.ErrNotFound
		}
		return issuer.Epoch{}, fmt.Errorf("pgstore: scan epoch: %w", err)
	}
	e.Number = uint64(num)
	e.MaxIndices = uint64(maxIdx)
	e.NextIdx = uint64(nextIdx)
	if closed != nil {
		e.ClosedAt = *closed
	}
	if lastIssued != nil {
		e.LastIssuedExpiresAt = *lastIssued
	}
	return e, nil
}

func scanEpochRowCols(rows pgx.Rows) (issuer.Epoch, error) {
	var (
		e               issuer.Epoch
		num             int64
		maxIdx, nextIdx int64
		closed          *time.Time
		lastIssued      *time.Time
	)
	if err := rows.Scan(&num, &maxIdx, &nextIdx, &e.OpenedAt, &closed, &lastIssued); err != nil {
		return issuer.Epoch{}, fmt.Errorf("pgstore: scan epoch: %w", err)
	}
	e.Number = uint64(num)
	e.MaxIndices = uint64(maxIdx)
	e.NextIdx = uint64(nextIdx)
	if closed != nil {
		e.ClosedAt = *closed
	}
	if lastIssued != nil {
		e.LastIssuedExpiresAt = *lastIssued
	}
	return e, nil
}

// RotateEpoch closes the current open epoch (if any) and opens a new one.
func (s *IssuerStore) RotateEpoch(ctx context.Context, maxIndices uint64, at time.Time) (issuer.Epoch, error) {
	if maxIndices == 0 {
		maxIndices = s.maxIndices
	}
	if at.IsZero() {
		at = time.Now()
	}
	var out issuer.Epoch
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var currentNum int64
		err := tx.QueryRow(
			ctx,
			`SELECT number FROM issuer_epochs WHERE closed_at IS NULL
			   ORDER BY number DESC LIMIT 1 FOR UPDATE`,
		).Scan(&currentNum)
		switch {
		case errors.Is(err, pgx.ErrNoRows):
			// No open epoch — opening the first one.
		case err != nil:
			return err
		default:
			if _, err := tx.Exec(
				ctx,
				`UPDATE issuer_epochs SET closed_at = $1 WHERE number = $2`,
				at.UTC(), currentNum,
			); err != nil {
				return err
			}
		}
		next := int64(1)
		if currentNum > 0 {
			next = currentNum + 1
		}
		if _, err := tx.Exec(
			ctx,
			`INSERT INTO issuer_epochs (number, max_indices, next_idx, opened_at)
			 VALUES ($1, $2, 0, $3)`,
			next, int64(maxIndices), at.UTC(),
		); err != nil {
			return err
		}
		out = issuer.Epoch{
			Number: uint64(next), MaxIndices: maxIndices, NextIdx: 0, OpenedAt: at,
		}
		return nil
	})
	if err != nil {
		return issuer.Epoch{}, fmt.Errorf("pgstore: rotate epoch: %w", err)
	}
	return out, nil
}

// AllocateIndex assigns the next free (epoch, idx) for a credential
// expiring at credentialExp. Auto-rotates when the open epoch fills up.
// Serialized via SELECT ... FOR UPDATE on the open epoch row.
func (s *IssuerStore) AllocateIndex(ctx context.Context, credentialExp time.Time) (uint64, uint64, error) {
	if credentialExp.IsZero() {
		return 0, 0, fmt.Errorf("%w: credentialExp required", issuer.ErrInvalid)
	}
	var (
		retEpoch uint64
		retIdx   uint64
	)
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var (
			num             int64
			maxIdx, nextIdx int64
			lastIssuedRaw   *time.Time
		)
		err := tx.QueryRow(
			ctx,
			`SELECT number, max_indices, next_idx, last_issued_expires_at
			   FROM issuer_epochs WHERE closed_at IS NULL
			   ORDER BY number DESC LIMIT 1 FOR UPDATE`,
		).Scan(&num, &maxIdx, &nextIdx, &lastIssuedRaw)
		if err != nil {
			return err
		}
		if nextIdx >= maxIdx {
			now := time.Now().UTC()
			if _, err := tx.Exec(ctx, `UPDATE issuer_epochs SET closed_at = $1 WHERE number = $2`, now, num); err != nil {
				return err
			}
			next := num + 1
			if _, err := tx.Exec(
				ctx,
				`INSERT INTO issuer_epochs (number, max_indices, next_idx, opened_at)
				 VALUES ($1, $2, 0, $3)`,
				next, int64(s.maxIndices), now,
			); err != nil {
				return err
			}
			num = next
			maxIdx = int64(s.maxIndices)
			nextIdx = 0
			lastIssuedRaw = nil
		}
		idx := nextIdx
		newNext := idx + 1
		lastExp := credentialExp.UTC()
		if lastIssuedRaw != nil && lastIssuedRaw.After(lastExp) {
			lastExp = *lastIssuedRaw
		}
		if _, err := tx.Exec(
			ctx,
			`UPDATE issuer_epochs SET next_idx = $1, last_issued_expires_at = $2
			   WHERE number = $3`,
			newNext, lastExp, num,
		); err != nil {
			return err
		}
		retEpoch = uint64(num)
		retIdx = uint64(idx)
		return nil
	})
	if err != nil {
		return 0, 0, fmt.Errorf("pgstore: allocate index: %w", err)
	}
	return retEpoch, retIdx, nil
}

// SetRevoked marks a credential as revoked. Idempotent — re-revoking the
// same idx is a no-op.
func (s *IssuerStore) SetRevoked(ctx context.Context, epoch, idx uint64, at time.Time) error {
	if at.IsZero() {
		at = time.Now()
	}
	e, err := s.GetEpoch(ctx, epoch)
	if err != nil {
		return err
	}
	if idx >= e.MaxIndices {
		return fmt.Errorf("%w: idx %d not in [0,%d)", issuer.ErrInvalid, idx, e.MaxIndices)
	}
	_, err = s.pool.Exec(
		ctx,
		`INSERT INTO issuer_revocations (epoch, idx, revoked_at) VALUES ($1, $2, $3)
		 ON CONFLICT (epoch, idx) DO NOTHING`,
		int64(epoch), int64(idx), at.UTC(),
	)
	if err != nil {
		return fmt.Errorf("pgstore: insert revocation: %w", err)
	}
	return nil
}

// LoadStatusBits materializes the bitstring for the named epoch from the
// sparse revocations table. Returns the raw bytes + the latest revocation
// timestamp.
func (s *IssuerStore) LoadStatusBits(ctx context.Context, epoch uint64) ([]byte, time.Time, error) {
	e, err := s.GetEpoch(ctx, epoch)
	if err != nil {
		return nil, time.Time{}, err
	}
	bits := make([]byte, (e.MaxIndices+7)/8)
	rows, err := s.pool.Query(
		ctx,
		`SELECT idx, revoked_at FROM issuer_revocations WHERE epoch = $1`, int64(epoch),
	)
	if err != nil {
		return nil, time.Time{}, fmt.Errorf("pgstore: select revocations: %w", err)
	}
	defer rows.Close()
	var latest time.Time
	for rows.Next() {
		var (
			idx int64
			at  time.Time
		)
		if err := rows.Scan(&idx, &at); err != nil {
			return nil, time.Time{}, fmt.Errorf("pgstore: scan revocation: %w", err)
		}
		if uint64(idx) >= e.MaxIndices {
			continue
		}
		bits[idx/8] |= 1 << (7 - uint(idx%8))
		if at.After(latest) {
			latest = at
		}
	}
	return bits, latest, rows.Err()
}

// Ensure IssuerStore satisfies issuer.Store at compile time.
var _ issuer.Store = (*IssuerStore)(nil)
