// SPDX-License-Identifier: MIT

package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/shadownet-protocol/shadownet/core/pkg/sca"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

// SCASessionStore implements sca.SessionStore against Postgres.
type SCASessionStore struct{ pool *pgxpool.Pool }

// NewSCASessionStore returns a SessionStore backed by pool.
func NewSCASessionStore(pool *pgxpool.Pool) *SCASessionStore { return &SCASessionStore{pool: pool} }

// Put implements sca.SessionStore.
func (s *SCASessionStore) Put(ctx context.Context, sess sca.Session) error {
	_, err := s.pool.Exec(
		ctx, `
INSERT INTO sca_sessions
  (id, subject, level, method, state, next_kind, next_url, next_ttl, callback_url,
   created_at, ready_at, expires_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
		sess.ID, sess.Subject, sess.Level, sess.Method, string(sess.State),
		nullableString(sess.Next.Kind), nullableString(sess.Next.URL), nullableInt(sess.Next.TTL), nullableString(sess.CallbackURL),
		sess.CreatedAt, nullableTime(sess.ReadyAt), sess.ExpiresAt,
	)
	if err != nil {
		return fmt.Errorf("pgstore: put session: %w", err)
	}
	return nil
}

// Get implements sca.SessionStore.
func (s *SCASessionStore) Get(ctx context.Context, id string) (sca.Session, error) {
	row := s.pool.QueryRow(ctx, `
SELECT id, subject, level, method, state, next_kind, next_url, next_ttl, callback_url,
       created_at, ready_at, expires_at
FROM sca_sessions WHERE id = $1`, id)
	var (
		sess     sca.Session
		state    string
		nextKind *string
		nextURL  *string
		nextTTL  *int
		cbURL    *string
		readyAt  *time.Time
	)
	if err := row.Scan(&sess.ID, &sess.Subject, &sess.Level, &sess.Method, &state,
		&nextKind, &nextURL, &nextTTL, &cbURL,
		&sess.CreatedAt, &readyAt, &sess.ExpiresAt); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return sca.Session{}, sca.ErrSessionNotFound
		}
		return sca.Session{}, fmt.Errorf("pgstore: get session: %w", err)
	}
	sess.State = sca.SessionState(state)
	sess.Next.Kind = derefString(nextKind)
	sess.Next.URL = derefString(nextURL)
	sess.Next.TTL = derefInt(nextTTL)
	sess.CallbackURL = derefString(cbURL)
	if readyAt != nil {
		sess.ReadyAt = readyAt.UTC()
	}
	sess.CreatedAt = sess.CreatedAt.UTC()
	sess.ExpiresAt = sess.ExpiresAt.UTC()
	return sess, nil
}

// MarkReady implements sca.SessionStore. Atomic transition pending → ready.
func (s *SCASessionStore) MarkReady(ctx context.Context, id string, at time.Time) error {
	tag, err := s.pool.Exec(ctx, `
UPDATE sca_sessions SET state='ready', ready_at=$2, expires_at=$3
WHERE id = $1 AND state='pending'`, id, at, at.Add(sca.ReadyTTL))
	if err != nil {
		return fmt.Errorf("pgstore: mark ready: %w", err)
	}
	return s.rowAffectedOrErr(ctx, id, tag.RowsAffected())
}

// Consume implements sca.SessionStore. Atomic transition ready → consumed.
func (s *SCASessionStore) Consume(ctx context.Context, id string) error {
	tag, err := s.pool.Exec(ctx, `
UPDATE sca_sessions SET state='consumed' WHERE id = $1 AND state='ready'`, id)
	if err != nil {
		return fmt.Errorf("pgstore: consume: %w", err)
	}
	return s.rowAffectedOrErr(ctx, id, tag.RowsAffected())
}

// Fail implements sca.SessionStore.
func (s *SCASessionStore) Fail(ctx context.Context, id string) error {
	tag, err := s.pool.Exec(ctx, `
UPDATE sca_sessions SET state = CASE state WHEN 'pending' THEN 'expired' ELSE 'failed' END
WHERE id = $1`, id)
	if err != nil {
		return fmt.Errorf("pgstore: fail: %w", err)
	}
	if tag.RowsAffected() == 0 {
		return sca.ErrSessionNotFound
	}
	return nil
}

// rowAffectedOrErr maps an UPDATE's RowsAffected into the right sentinel.
// 0 rows ⇒ either the row didn't exist (ErrSessionNotFound) or it existed
// but was in the wrong state (ErrSessionState); a follow-up SELECT
// distinguishes.
func (s *SCASessionStore) rowAffectedOrErr(ctx context.Context, id string, affected int64) error {
	if affected > 0 {
		return nil
	}
	var found int
	err := s.pool.QueryRow(ctx, `SELECT 1 FROM sca_sessions WHERE id = $1`, id).Scan(&found)
	if errors.Is(err, pgx.ErrNoRows) {
		return sca.ErrSessionNotFound
	}
	if err != nil {
		return fmt.Errorf("pgstore: state check: %w", err)
	}
	return sca.ErrSessionState
}

// SCAIssuanceStore implements sca.IssuanceStore against Postgres.
type SCAIssuanceStore struct{ pool *pgxpool.Pool }

// NewSCAIssuanceStore returns an IssuanceStore backed by pool.
func NewSCAIssuanceStore(pool *pgxpool.Pool) *SCAIssuanceStore { return &SCAIssuanceStore{pool: pool} }

// Put implements sca.IssuanceStore.
func (s *SCAIssuanceStore) Put(ctx context.Context, c sca.IssuedCredential) error {
	_, err := s.pool.Exec(
		ctx, `
INSERT INTO sca_credentials
  (jti, issuer, subject, level, subject_type, jwt, status_list_id, status_list_index,
   issued_at, expires)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		c.JTI, c.Issuer, c.Subject, c.Level, string(c.SubjectType), c.JWT,
		c.StatusListID, int64(c.StatusListIndex), c.IssuedAt, c.Expires,
	)
	if err != nil {
		return fmt.Errorf("pgstore: put credential: %w", err)
	}
	return nil
}

// Get implements sca.IssuanceStore.
func (s *SCAIssuanceStore) Get(ctx context.Context, jti string) (sca.IssuedCredential, error) {
	var (
		c        sca.IssuedCredential
		subjType string
		idx      int64
	)
	err := s.pool.QueryRow(ctx, `
SELECT jti, issuer, subject, level, subject_type, jwt, status_list_id, status_list_index, issued_at, expires
FROM sca_credentials WHERE jti = $1`, jti).Scan(
		&c.JTI, &c.Issuer, &c.Subject, &c.Level, &subjType, &c.JWT,
		&c.StatusListID, &idx, &c.IssuedAt, &c.Expires,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return sca.IssuedCredential{}, sca.ErrJTINotFound
	}
	if err != nil {
		return sca.IssuedCredential{}, fmt.Errorf("pgstore: get credential: %w", err)
	}
	c.SubjectType = vc.SubjectType(subjType)
	c.StatusListIndex = uint64(idx)
	c.IssuedAt = c.IssuedAt.UTC()
	c.Expires = c.Expires.UTC()
	return c, nil
}

// SCARevocationStore implements sca.RevocationStore against Postgres with
// rotating, sparse status lists. AssignIndex is atomic via
// UPDATE … RETURNING; Revoke is idempotent via ON CONFLICT DO NOTHING; both
// avoid the silent-revocation-loss race a BLOB-based fetch-modify-write
// would have under multi-writer Postgres.
type SCARevocationStore struct {
	pool     *pgxpool.Pool
	baseID   string
	capacity int64
}

// NewSCARevocationStore returns a rotating RevocationStore backed by pool.
// baseID names the first list shard; subsequent shards are `<baseID>-2`,
// `<baseID>-3`, … Capacity defaults to 131072 bits.
func NewSCARevocationStore(pool *pgxpool.Pool, baseID string, capacity uint64) *SCARevocationStore {
	if capacity == 0 {
		capacity = 131_072
	}
	return &SCARevocationStore{pool: pool, baseID: baseID, capacity: int64(capacity)}
}

func (s *SCARevocationStore) listOrdinalName(i int) string {
	if i == 0 {
		return s.baseID
	}
	return fmt.Sprintf("%s-%d", s.baseID, i+1)
}

// AssignIndex implements sca.RevocationStore. Hot path: atomic
// UPDATE … RETURNING on the active (least-recently-created, non-full) shard.
// Cold path (active shard full or no shards yet): allocate a new shard via
// INSERT … ON CONFLICT DO NOTHING and retry the loop on conflict.
//
// Concurrency notes: the inner SELECT uses FOR UPDATE (without SKIP LOCKED)
// so concurrent callers queue on the row-level lock instead of falsely
// concluding "no active shard." When a shard fills, multiple callers may
// race on shard allocation; ON CONFLICT lets exactly one win, and the loop
// retries the hot path against whichever shard the winner created.
func (s *SCARevocationStore) AssignIndex(ctx context.Context) (string, uint64, error) {
	const maxAttempts = 8
	for attempt := 0; attempt < maxAttempts; attempt++ {
		var (
			listID  string
			nextIdx int64
		)
		err := s.pool.QueryRow(ctx, `
UPDATE sca_status_lists
SET    next_index = next_index + 1
WHERE  list_id = (
    SELECT list_id FROM sca_status_lists
    WHERE  next_index < size
    ORDER  BY created_at, list_id
    LIMIT  1
    FOR UPDATE
)
RETURNING list_id, next_index - 1`).Scan(&listID, &nextIdx)
		if err == nil {
			return listID, uint64(nextIdx), nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return "", 0, fmt.Errorf("pgstore: assign index: %w", err)
		}

		var count int
		if err := s.pool.QueryRow(ctx,
			`SELECT COUNT(*) FROM sca_status_lists`).Scan(&count); err != nil {
			return "", 0, fmt.Errorf("pgstore: count shards: %w", err)
		}
		candidate := s.listOrdinalName(count)
		cmd, err := s.pool.Exec(ctx, `
INSERT INTO sca_status_lists (list_id, size, next_index)
VALUES ($1, $2, 1)
ON CONFLICT (list_id) DO NOTHING`, candidate, s.capacity)
		if err != nil {
			return "", 0, fmt.Errorf("pgstore: create shard: %w", err)
		}
		if cmd.RowsAffected() == 1 {
			return candidate, 0, nil
		}
	}
	return "", 0, errors.New("pgstore: assign index: too many concurrent rotations")
}

// Revoke implements sca.RevocationStore. Idempotent.
func (s *SCARevocationStore) Revoke(ctx context.Context, listID string, index uint64) error {
	// First confirm the shard exists; FK on sca_revoked would surface as a
	// generic insert error otherwise.
	var exists bool
	err := s.pool.QueryRow(ctx,
		`SELECT EXISTS(SELECT 1 FROM sca_status_lists WHERE list_id = $1)`, listID).Scan(&exists)
	if err != nil {
		return fmt.Errorf("pgstore: lookup shard: %w", err)
	}
	if !exists {
		return sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	if _, err := s.pool.Exec(ctx, `
INSERT INTO sca_revoked (list_id, idx) VALUES ($1, $2)
ON CONFLICT DO NOTHING`, listID, int64(index)); err != nil {
		return fmt.Errorf("pgstore: revoke: %w", err)
	}
	return nil
}

// Snapshot implements sca.RevocationStore. Reconstructs a vc.StatusList of
// the shard's full bit width with revoked indices set.
func (s *SCARevocationStore) Snapshot(ctx context.Context, listID string) (*vc.StatusList, error) {
	var size int64
	err := s.pool.QueryRow(ctx, `SELECT size FROM sca_status_lists WHERE list_id = $1`, listID).Scan(&size)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	if err != nil {
		return nil, fmt.Errorf("pgstore: shard size: %w", err)
	}
	rows, err := s.pool.Query(ctx, `SELECT idx FROM sca_revoked WHERE list_id = $1`, listID)
	if err != nil {
		return nil, fmt.Errorf("pgstore: query revoked: %w", err)
	}
	defer rows.Close()
	list := vc.NewStatusList(uint64(size))
	for rows.Next() {
		var idx int64
		if err := rows.Scan(&idx); err != nil {
			return nil, fmt.Errorf("pgstore: scan revoked: %w", err)
		}
		if err := list.Set(uint64(idx), true); err != nil {
			return nil, err
		}
	}
	return list, rows.Err()
}

func nullableString(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func nullableInt(i int) *int {
	if i == 0 {
		return nil
	}
	return &i
}

func nullableTime(t time.Time) *time.Time {
	if t.IsZero() {
		return nil
	}
	return &t
}

func derefString(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func derefInt(p *int) int {
	if p == nil {
		return 0
	}
	return *p
}
