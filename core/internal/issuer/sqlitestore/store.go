// SPDX-License-Identifier: MIT

// Package sqlitestore is the default Issuer Store implementation backed
// by a single SQLite database file. The schema is auto-applied on Open;
// migrations are forward-only.
package sqlitestore

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	// modernc.org/sqlite registers the "sqlite" sql.DB driver via init();
	// this package is non-main so revive demands the justification.
	_ "modernc.org/sqlite"

	"github.com/shadownet-protocol/shadownet/core/internal/issuer"
)

const schema = `
CREATE TABLE IF NOT EXISTS issuer_credentials (
    idempotency_key   TEXT PRIMARY KEY,
    jws               TEXT NOT NULL,
    iss               TEXT NOT NULL,
    sub               TEXT NOT NULL,
    org               TEXT NOT NULL,
    epoch             INTEGER NOT NULL,
    idx               INTEGER NOT NULL,
    issued_at_unix    INTEGER NOT NULL,
    expires_at_unix   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issuer_credentials_sub ON issuer_credentials(sub);

CREATE TABLE IF NOT EXISTS issuer_pendings (
    handle_id              TEXT PRIMARY KEY,
    idempotency_key        TEXT NOT NULL UNIQUE,
    iss                    TEXT NOT NULL,
    aud                    TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    org                    TEXT NOT NULL,
    subject_pub            TEXT NOT NULL,
    status                 INTEGER NOT NULL,
    next_url               TEXT NOT NULL DEFAULT '',
    reason                 TEXT NOT NULL DEFAULT '',
    created_at_unix        INTEGER NOT NULL,
    updated_at_unix        INTEGER NOT NULL,
    ceremony_expiry_unix   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issuer_pendings_status ON issuer_pendings(status);

CREATE TABLE IF NOT EXISTS issuer_epochs (
    number                       INTEGER PRIMARY KEY,
    max_indices                  INTEGER NOT NULL,
    next_idx                     INTEGER NOT NULL,
    opened_at_unix               INTEGER NOT NULL,
    closed_at_unix               INTEGER NOT NULL DEFAULT 0,
    last_issued_expires_at_unix  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS issuer_revocations (
    epoch             INTEGER NOT NULL,
    idx               INTEGER NOT NULL,
    revoked_at_unix   INTEGER NOT NULL,
    PRIMARY KEY (epoch, idx)
);
`

// DefaultMaxIndices is the default ceiling on a single epoch's bitstring
// allocation. 2^17 = 131072 indices fits in 16 KiB raw — small enough to
// gzip + base64url efficiently and large enough that a working Hub can
// run for months between rotations.
const DefaultMaxIndices uint64 = 131072

// Store implements issuer.Store backed by SQLite.
type Store struct {
	db         *sql.DB
	maxIndices uint64
}

// Open initializes a Store at the given DSN ("file:./issuer.db",
// ":memory:", etc.). MaxIndices controls the ceiling new epochs are
// opened with; pass 0 for DefaultMaxIndices.
func Open(dsn string, maxIndices uint64) (*Store, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("sqlitestore: open: %w", err)
	}
	if err := db.PingContext(context.Background()); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: ping: %w", err)
	}
	// Foreign keys + WAL aren't strictly required, but they improve
	// concurrency on multi-reader workloads (status-list fetches running
	// alongside CSR issuance).
	if _, err := db.Exec("PRAGMA foreign_keys = ON;"); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: pragma: %w", err)
	}
	if _, err := db.Exec("PRAGMA journal_mode = WAL;"); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: pragma WAL: %w", err)
	}
	// busy_timeout makes SQLITE_BUSY blocking up to N ms, which lets
	// concurrent AllocateIndex callers serialize politely rather than
	// surfacing transient lock contention to the caller.
	if _, err := db.Exec("PRAGMA busy_timeout = 5000;"); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: pragma busy_timeout: %w", err)
	}
	// SQLite serializes writers; multiple Go-level connections cause
	// transient SQLITE_BUSY on the write path even with busy_timeout.
	// Pin the pool to one connection so AllocateIndex + RotateEpoch +
	// PutCredential all queue through a single SQLite writer. For the
	// production Postgres path see core/pgstore.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: apply schema: %w", err)
	}
	if maxIndices == 0 {
		maxIndices = DefaultMaxIndices
	}
	s := &Store{db: db, maxIndices: maxIndices}
	if err := s.ensureCurrentEpoch(context.Background()); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("sqlitestore: bootstrap epoch: %w", err)
	}
	return s, nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

// Ping returns nil when the database is reachable.
func (s *Store) Ping(ctx context.Context) error { return s.db.PingContext(ctx) }

func (s *Store) ensureCurrentEpoch(ctx context.Context) error {
	var n int
	if err := s.db.QueryRowContext(
		ctx,
		`SELECT COUNT(*) FROM issuer_epochs WHERE closed_at_unix = 0`,
	).Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	now := time.Now().Unix()
	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO issuer_epochs (number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix)
		 VALUES (1, ?, 0, ?, 0, 0)`,
		s.maxIndices, now,
	)
	return err
}

// PutCredential inserts a Credential or returns an error if the
// idempotency key is already taken (use GetByIdempotencyKey first).
func (s *Store) PutCredential(ctx context.Context, c issuer.Credential) error {
	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO issuer_credentials
		    (idempotency_key, jws, iss, sub, org, epoch, idx, issued_at_unix, expires_at_unix)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		c.IdempotencyKey, c.JWS, c.Iss, c.Sub, c.Org, c.Epoch, c.Idx,
		c.IssuedAt.Unix(), c.ExpiresAt.Unix(),
	)
	if err != nil {
		return fmt.Errorf("sqlitestore: insert credential: %w", err)
	}
	return nil
}

// GetByIdempotencyKey returns the prior credential issued under key, or
// issuer.ErrNotFound. The returned Credential carries the verbatim JWS
// bytes so the handler re-emits the original on idempotent re-POST.
func (s *Store) GetByIdempotencyKey(ctx context.Context, key string) (issuer.Credential, error) {
	var c issuer.Credential
	var issued, exp int64
	err := s.db.QueryRowContext(
		ctx,
		`SELECT idempotency_key, jws, iss, sub, org, epoch, idx, issued_at_unix, expires_at_unix
		   FROM issuer_credentials WHERE idempotency_key = ?`,
		key,
	).Scan(&c.IdempotencyKey, &c.JWS, &c.Iss, &c.Sub, &c.Org, &c.Epoch, &c.Idx, &issued, &exp)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return issuer.Credential{}, issuer.ErrNotFound
	case err != nil:
		return issuer.Credential{}, fmt.Errorf("sqlitestore: select credential: %w", err)
	}
	c.IssuedAt = time.Unix(issued, 0)
	c.ExpiresAt = time.Unix(exp, 0)
	return c, nil
}

// PutPending inserts or replaces a Pending. The unique idempotency_key
// guards against duplicate parks for the same logical CSR.
func (s *Store) PutPending(ctx context.Context, p issuer.Pending) error {
	now := time.Now().Unix()
	if p.CreatedAt.IsZero() {
		p.CreatedAt = time.Unix(now, 0)
	}
	if p.UpdatedAt.IsZero() {
		p.UpdatedAt = time.Unix(now, 0)
	}
	_, err := s.db.ExecContext(
		ctx,
		`INSERT INTO issuer_pendings
		    (handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
		     next_url, reason, created_at_unix, updated_at_unix, ceremony_expiry_unix)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(handle_id) DO UPDATE SET
		    status = excluded.status,
		    next_url = excluded.next_url,
		    reason = excluded.reason,
		    updated_at_unix = excluded.updated_at_unix`,
		p.HandleID, p.IdempotencyKey, p.Iss, p.Aud, p.Kind, p.Org, p.SubjectPubKey, int(p.Status),
		p.NextURL, p.Reason, p.CreatedAt.Unix(), p.UpdatedAt.Unix(), p.CeremonyExpiry.Unix(),
	)
	if err != nil {
		return fmt.Errorf("sqlitestore: upsert pending: %w", err)
	}
	return nil
}

// GetPending returns the Pending keyed by handleID or issuer.ErrNotFound.
func (s *Store) GetPending(ctx context.Context, handleID string) (issuer.Pending, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
		        next_url, reason, created_at_unix, updated_at_unix, ceremony_expiry_unix
		   FROM issuer_pendings WHERE handle_id = ?`, handleID)
	return scanPending(row)
}

// GetPendingByIdempotencyKey is the idempotent-lookup variant used by the
// HTTP handler to detect a re-POST.
func (s *Store) GetPendingByIdempotencyKey(ctx context.Context, key string) (issuer.Pending, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
		        next_url, reason, created_at_unix, updated_at_unix, ceremony_expiry_unix
		   FROM issuer_pendings WHERE idempotency_key = ?`, key)
	return scanPending(row)
}

func scanPending(row *sql.Row) (issuer.Pending, error) {
	var p issuer.Pending
	var status int
	var created, updated, ceremonyExp int64
	if err := row.Scan(
		&p.HandleID, &p.IdempotencyKey, &p.Iss, &p.Aud, &p.Kind, &p.Org,
		&p.SubjectPubKey, &status, &p.NextURL, &p.Reason, &created, &updated, &ceremonyExp,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return issuer.Pending{}, issuer.ErrNotFound
		}
		return issuer.Pending{}, fmt.Errorf("sqlitestore: scan pending: %w", err)
	}
	p.Status = issuer.PendingStatus(status)
	p.CreatedAt = time.Unix(created, 0)
	p.UpdatedAt = time.Unix(updated, 0)
	p.CeremonyExpiry = time.Unix(ceremonyExp, 0)
	return p, nil
}

// ListPending returns Pendings matching the filter. Default order is
// created-at ascending (oldest first) so the admin queue surface is
// predictable.
func (s *Store) ListPending(ctx context.Context, filter issuer.PendingFilter) ([]issuer.Pending, error) {
	q := `SELECT handle_id, idempotency_key, iss, aud, kind, org, subject_pub, status,
	             next_url, reason, created_at_unix, updated_at_unix, ceremony_expiry_unix
	        FROM issuer_pendings WHERE 1=1`
	args := make([]any, 0, 4)
	if filter.Status != nil {
		q += " AND status = ?"
		args = append(args, int(*filter.Status))
	}
	if !filter.IncludeExpired {
		q += " AND ceremony_expiry_unix > ?"
		args = append(args, time.Now().Unix())
	}
	q += " ORDER BY created_at_unix ASC"
	if filter.Limit > 0 {
		q += " LIMIT ?"
		args = append(args, filter.Limit)
	}
	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("sqlitestore: list pending: %w", err)
	}
	defer rows.Close()
	var out []issuer.Pending
	for rows.Next() {
		var p issuer.Pending
		var status int
		var created, updated, ceremonyExp int64
		if err := rows.Scan(
			&p.HandleID, &p.IdempotencyKey, &p.Iss, &p.Aud, &p.Kind, &p.Org,
			&p.SubjectPubKey, &status, &p.NextURL, &p.Reason, &created, &updated, &ceremonyExp,
		); err != nil {
			return nil, fmt.Errorf("sqlitestore: scan pending row: %w", err)
		}
		p.Status = issuer.PendingStatus(status)
		p.CreatedAt = time.Unix(created, 0)
		p.UpdatedAt = time.Unix(updated, 0)
		p.CeremonyExpiry = time.Unix(ceremonyExp, 0)
		out = append(out, p)
	}
	return out, rows.Err()
}

// UpdatePendingStatus advances a parked ceremony's lifecycle state.
func (s *Store) UpdatePendingStatus(ctx context.Context, handleID string, status issuer.PendingStatus, reason string, at time.Time) error {
	if at.IsZero() {
		at = time.Now()
	}
	res, err := s.db.ExecContext(
		ctx,
		`UPDATE issuer_pendings SET status = ?, reason = ?, updated_at_unix = ? WHERE handle_id = ?`,
		int(status), reason, at.Unix(), handleID,
	)
	if err != nil {
		return fmt.Errorf("sqlitestore: update pending: %w", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return issuer.ErrNotFound
	}
	return nil
}

// CurrentEpoch returns the single open epoch.
func (s *Store) CurrentEpoch(ctx context.Context) (issuer.Epoch, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix
		   FROM issuer_epochs WHERE closed_at_unix = 0
		   ORDER BY number DESC LIMIT 1`)
	return scanEpoch(row)
}

// GetEpoch returns the epoch by number.
func (s *Store) GetEpoch(ctx context.Context, n uint64) (issuer.Epoch, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix
		   FROM issuer_epochs WHERE number = ?`, n)
	return scanEpoch(row)
}

// ListOpenEpochs returns all epochs that have not been closed yet. In
// normal operation this is exactly one epoch; the API exists for invariants
// + admin tooling.
func (s *Store) ListOpenEpochs(ctx context.Context) ([]issuer.Epoch, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix
		   FROM issuer_epochs WHERE closed_at_unix = 0 ORDER BY number ASC`)
	if err != nil {
		return nil, fmt.Errorf("sqlitestore: list epochs: %w", err)
	}
	defer rows.Close()
	var out []issuer.Epoch
	for rows.Next() {
		var e issuer.Epoch
		var closed, lastIssued int64
		if err := rows.Scan(&e.Number, &e.MaxIndices, &e.NextIdx, &openedScan{&e.OpenedAt}, &closed, &lastIssued); err != nil {
			return nil, fmt.Errorf("sqlitestore: scan epoch: %w", err)
		}
		if closed != 0 {
			e.ClosedAt = time.Unix(closed, 0)
		}
		if lastIssued != 0 {
			e.LastIssuedExpiresAt = time.Unix(lastIssued, 0)
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// openedScan is a tiny adapter that lets time.Time be Scan'd from an int64
// unix-seconds column.
type openedScan struct{ t *time.Time }

func (o *openedScan) Scan(src any) error {
	switch v := src.(type) {
	case int64:
		*o.t = time.Unix(v, 0)
		return nil
	case nil:
		return nil
	default:
		return fmt.Errorf("openedScan: unsupported source type %T", src)
	}
}

func scanEpoch(row *sql.Row) (issuer.Epoch, error) {
	var e issuer.Epoch
	var opened, closed, lastIssued int64
	if err := row.Scan(&e.Number, &e.MaxIndices, &e.NextIdx, &opened, &closed, &lastIssued); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return issuer.Epoch{}, issuer.ErrNotFound
		}
		return issuer.Epoch{}, fmt.Errorf("sqlitestore: scan epoch: %w", err)
	}
	e.OpenedAt = time.Unix(opened, 0)
	if closed != 0 {
		e.ClosedAt = time.Unix(closed, 0)
	}
	if lastIssued != 0 {
		e.LastIssuedExpiresAt = time.Unix(lastIssued, 0)
	}
	return e, nil
}

// RotateEpoch closes the current open epoch and opens a new one with the
// given max-indices cap. Concurrency-safe: serialized via BEGIN IMMEDIATE.
func (s *Store) RotateEpoch(ctx context.Context, maxIndices uint64, at time.Time) (issuer.Epoch, error) {
	if maxIndices == 0 {
		maxIndices = s.maxIndices
	}
	if at.IsZero() {
		at = time.Now()
	}
	tx, err := s.db.BeginTx(ctx, &sql.TxOptions{})
	if err != nil {
		return issuer.Epoch{}, fmt.Errorf("sqlitestore: begin rotate: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	// Lock contention on writers — BEGIN IMMEDIATE prevents WAL races.
	if _, err := tx.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil && !errors.Is(err, sql.ErrTxDone) {
		// SQLite already started a tx via BeginTx; the additional BEGIN
		// IMMEDIATE will sometimes fail with "cannot start a transaction
		// within a transaction" — that's fine, the outer tx already
		// holds the reserved lock.
		_ = err
	}

	current, err := s.currentEpochInTx(ctx, tx)
	if err != nil && !errors.Is(err, issuer.ErrNotFound) {
		return issuer.Epoch{}, err
	}
	if err == nil {
		if _, err := tx.ExecContext(
			ctx,
			`UPDATE issuer_epochs SET closed_at_unix = ? WHERE number = ?`,
			at.Unix(), current.Number,
		); err != nil {
			return issuer.Epoch{}, fmt.Errorf("sqlitestore: close epoch: %w", err)
		}
	}
	next := uint64(1)
	if err == nil {
		next = current.Number + 1
	}
	if _, err := tx.ExecContext(
		ctx,
		`INSERT INTO issuer_epochs (number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix)
		 VALUES (?, ?, 0, ?, 0, 0)`,
		next, maxIndices, at.Unix(),
	); err != nil {
		return issuer.Epoch{}, fmt.Errorf("sqlitestore: open new epoch: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return issuer.Epoch{}, fmt.Errorf("sqlitestore: commit rotate: %w", err)
	}
	return issuer.Epoch{
		Number: next, MaxIndices: maxIndices, NextIdx: 0, OpenedAt: at,
	}, nil
}

func (s *Store) currentEpochInTx(ctx context.Context, tx *sql.Tx) (issuer.Epoch, error) {
	row := tx.QueryRowContext(ctx,
		`SELECT number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix
		   FROM issuer_epochs WHERE closed_at_unix = 0
		   ORDER BY number DESC LIMIT 1`)
	var e issuer.Epoch
	var opened, closed, lastIssued int64
	if err := row.Scan(&e.Number, &e.MaxIndices, &e.NextIdx, &opened, &closed, &lastIssued); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return issuer.Epoch{}, issuer.ErrNotFound
		}
		return issuer.Epoch{}, err
	}
	e.OpenedAt = time.Unix(opened, 0)
	if closed != 0 {
		e.ClosedAt = time.Unix(closed, 0)
	}
	if lastIssued != 0 {
		e.LastIssuedExpiresAt = time.Unix(lastIssued, 0)
	}
	return e, nil
}

// AllocateIndex assigns the next free (epoch, idx) under the open epoch.
// Auto-rotates when the open epoch's max_indices is exhausted, recording
// the credential's expiry for safe GC later.
func (s *Store) AllocateIndex(ctx context.Context, credentialExp time.Time) (uint64, uint64, error) {
	if credentialExp.IsZero() {
		return 0, 0, fmt.Errorf("%w: credentialExp required", issuer.ErrInvalid)
	}
	tx, err := s.db.BeginTx(ctx, &sql.TxOptions{})
	if err != nil {
		return 0, 0, fmt.Errorf("sqlitestore: begin alloc: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	current, err := s.currentEpochInTx(ctx, tx)
	if err != nil {
		return 0, 0, err
	}

	if current.NextIdx >= current.MaxIndices {
		// Roll the epoch inline. Close the current, open a new one,
		// re-fetch.
		now := time.Now()
		if _, err := tx.ExecContext(
			ctx,
			`UPDATE issuer_epochs SET closed_at_unix = ? WHERE number = ?`,
			now.Unix(), current.Number,
		); err != nil {
			return 0, 0, fmt.Errorf("sqlitestore: close exhausted epoch: %w", err)
		}
		if _, err := tx.ExecContext(
			ctx,
			`INSERT INTO issuer_epochs (number, max_indices, next_idx, opened_at_unix, closed_at_unix, last_issued_expires_at_unix)
			 VALUES (?, ?, 0, ?, 0, 0)`,
			current.Number+1, s.maxIndices, now.Unix(),
		); err != nil {
			return 0, 0, fmt.Errorf("sqlitestore: open replacement epoch: %w", err)
		}
		current = issuer.Epoch{
			Number: current.Number + 1, MaxIndices: s.maxIndices, NextIdx: 0, OpenedAt: now,
		}
	}

	idx := current.NextIdx
	newNext := idx + 1
	newLastExp := current.LastIssuedExpiresAt
	if credentialExp.After(newLastExp) {
		newLastExp = credentialExp
	}
	if _, err := tx.ExecContext(
		ctx,
		`UPDATE issuer_epochs SET next_idx = ?, last_issued_expires_at_unix = ?
		   WHERE number = ?`,
		newNext, newLastExp.Unix(), current.Number,
	); err != nil {
		return 0, 0, fmt.Errorf("sqlitestore: bump next_idx: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return 0, 0, fmt.Errorf("sqlitestore: commit alloc: %w", err)
	}
	return current.Number, idx, nil
}

// SetRevoked marks a credential as revoked.
func (s *Store) SetRevoked(ctx context.Context, epoch, idx uint64, at time.Time) error {
	if at.IsZero() {
		at = time.Now()
	}
	// First confirm the epoch + idx is within range.
	e, err := s.GetEpoch(ctx, epoch)
	if err != nil {
		return err
	}
	if idx >= e.MaxIndices {
		return fmt.Errorf("%w: idx %d not in [0,%d)", issuer.ErrInvalid, idx, e.MaxIndices)
	}
	_, err = s.db.ExecContext(
		ctx,
		`INSERT OR IGNORE INTO issuer_revocations (epoch, idx, revoked_at_unix) VALUES (?, ?, ?)`,
		epoch, idx, at.Unix(),
	)
	if err != nil {
		return fmt.Errorf("sqlitestore: insert revocation: %w", err)
	}
	return nil
}

// LoadStatusBits materializes the bitstring for the named epoch from the
// sparse revocations table.
func (s *Store) LoadStatusBits(ctx context.Context, epoch uint64) ([]byte, time.Time, error) {
	e, err := s.GetEpoch(ctx, epoch)
	if err != nil {
		return nil, time.Time{}, err
	}
	bits := make([]byte, (e.MaxIndices+7)/8)
	rows, err := s.db.QueryContext(ctx,
		`SELECT idx, revoked_at_unix FROM issuer_revocations WHERE epoch = ?`, epoch)
	if err != nil {
		return nil, time.Time{}, fmt.Errorf("sqlitestore: select revocations: %w", err)
	}
	defer rows.Close()
	var latest time.Time
	for rows.Next() {
		var idx uint64
		var when int64
		if err := rows.Scan(&idx, &when); err != nil {
			return nil, time.Time{}, fmt.Errorf("sqlitestore: scan revocation: %w", err)
		}
		if idx >= e.MaxIndices {
			continue
		}
		bits[idx/8] |= 1 << (7 - uint(idx%8))
		if t := time.Unix(when, 0); t.After(latest) {
			latest = t
		}
	}
	return bits, latest, rows.Err()
}
