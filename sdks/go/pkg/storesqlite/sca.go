// SPDX-License-Identifier: MIT

package storesqlite

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	// Pure-Go SQLite driver registered with database/sql; the package only
	// runs init() and exposes nothing we call directly.
	_ "modernc.org/sqlite"

	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
	"github.com/shadownet-protocol/shadownet-go/pkg/vc"
)

const scaSchema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
  id           TEXT PRIMARY KEY,
  subject      TEXT NOT NULL,
  level        TEXT NOT NULL,
  method       TEXT NOT NULL,
  state        TEXT NOT NULL,
  next_kind    TEXT,
  next_url     TEXT,
  next_ttl     INTEGER,
  callback_url TEXT,
  created_at   INTEGER NOT NULL,
  ready_at     INTEGER,
  expires_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
  jti               TEXT PRIMARY KEY,
  issuer            TEXT NOT NULL,
  subject           TEXT NOT NULL,
  level             TEXT NOT NULL,
  subject_type      TEXT NOT NULL,
  jwt               TEXT NOT NULL,
  status_list_id    TEXT NOT NULL,
  status_list_index INTEGER NOT NULL,
  issued_at         INTEGER NOT NULL,
  expires           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS status_lists (
  list_id    TEXT PRIMARY KEY,
  bits       BLOB    NOT NULL,
  next_index INTEGER NOT NULL,
  size       INTEGER NOT NULL
);
`

// Open returns a *sql.DB connected to dsn (a file path or :memory:) with the
// SCA schema applied.
func Open(dsn string) (*sql.DB, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("storesqlite: open: %w", err)
	}
	db.SetMaxOpenConns(1) // serialize writes; sqlite supports many readers but one writer
	if _, err := db.Exec(scaSchema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("storesqlite: apply schema: %w", err)
	}
	return db, nil
}

// SCASessionStore implements sca.SessionStore against SQLite.
type SCASessionStore struct{ db *sql.DB }

// NewSCASessionStore returns a SessionStore backed by db.
func NewSCASessionStore(db *sql.DB) *SCASessionStore { return &SCASessionStore{db: db} }

// Put implements sca.SessionStore.
func (s *SCASessionStore) Put(ctx context.Context, sess sca.Session) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO sessions (id, subject, level, method, state, next_kind, next_url, next_ttl, callback_url, created_at, ready_at, expires_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		sess.ID, sess.Subject, sess.Level, sess.Method, string(sess.State),
		sess.Next.Kind, sess.Next.URL, sess.Next.TTL, sess.CallbackURL,
		sess.CreatedAt.Unix(), nullableUnix(sess.ReadyAt), sess.ExpiresAt.Unix())
	if err != nil {
		return fmt.Errorf("storesqlite: put session: %w", err)
	}
	return nil
}

// Get implements sca.SessionStore.
func (s *SCASessionStore) Get(ctx context.Context, id string) (sca.Session, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT id, subject, level, method, state, next_kind, next_url, next_ttl, callback_url, created_at, ready_at, expires_at
FROM sessions WHERE id = ?`, id)
	var (
		sess     sca.Session
		state    string
		nextKind sql.NullString
		nextURL  sql.NullString
		nextTTL  sql.NullInt64
		cbURL    sql.NullString
		createdU int64
		readyU   sql.NullInt64
		expU     int64
	)
	if err := row.Scan(&sess.ID, &sess.Subject, &sess.Level, &sess.Method, &state,
		&nextKind, &nextURL, &nextTTL, &cbURL, &createdU, &readyU, &expU); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return sca.Session{}, sca.ErrSessionNotFound
		}
		return sca.Session{}, fmt.Errorf("storesqlite: get session: %w", err)
	}
	sess.State = sca.SessionState(state)
	sess.Next.Kind = nextKind.String
	sess.Next.URL = nextURL.String
	sess.Next.TTL = int(nextTTL.Int64)
	sess.CallbackURL = cbURL.String
	sess.CreatedAt = time.Unix(createdU, 0).UTC()
	if readyU.Valid {
		sess.ReadyAt = time.Unix(readyU.Int64, 0).UTC()
	}
	sess.ExpiresAt = time.Unix(expU, 0).UTC()
	return sess, nil
}

// MarkReady implements sca.SessionStore.
func (s *SCASessionStore) MarkReady(ctx context.Context, id string, at time.Time) error {
	res, err := s.db.ExecContext(ctx, `
UPDATE sessions SET state='ready', ready_at=?, expires_at=?
WHERE id = ? AND state='pending'`, at.Unix(), at.Add(sca.ReadyTTL).Unix(), id)
	if err != nil {
		return fmt.Errorf("storesqlite: mark ready: %w", err)
	}
	return rowsAffectedOrErr(ctx, s.db, res, sca.ErrSessionState, sca.ErrSessionNotFound, "sessions", id)
}

// Consume implements sca.SessionStore.
func (s *SCASessionStore) Consume(ctx context.Context, id string) error {
	res, err := s.db.ExecContext(ctx, `UPDATE sessions SET state='consumed' WHERE id = ? AND state='ready'`, id)
	if err != nil {
		return fmt.Errorf("storesqlite: consume: %w", err)
	}
	return rowsAffectedOrErr(ctx, s.db, res, sca.ErrSessionState, sca.ErrSessionNotFound, "sessions", id)
}

// Fail implements sca.SessionStore.
func (s *SCASessionStore) Fail(ctx context.Context, id string) error {
	res, err := s.db.ExecContext(ctx, `
UPDATE sessions SET state = CASE state WHEN 'pending' THEN 'expired' ELSE 'failed' END
WHERE id = ?`, id)
	if err != nil {
		return fmt.Errorf("storesqlite: fail: %w", err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return sca.ErrSessionNotFound
	}
	return nil
}

// SCAIssuanceStore implements sca.IssuanceStore against SQLite.
type SCAIssuanceStore struct{ db *sql.DB }

// NewSCAIssuanceStore returns an IssuanceStore backed by db.
func NewSCAIssuanceStore(db *sql.DB) *SCAIssuanceStore { return &SCAIssuanceStore{db: db} }

// Put implements sca.IssuanceStore.
func (s *SCAIssuanceStore) Put(ctx context.Context, c sca.IssuedCredential) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO credentials (jti, issuer, subject, level, subject_type, jwt, status_list_id, status_list_index, issued_at, expires)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		c.JTI, c.Issuer, c.Subject, c.Level, string(c.SubjectType), c.JWT,
		c.StatusListID, int64(c.StatusListIndex), c.IssuedAt.Unix(), c.Expires.Unix())
	if err != nil {
		return fmt.Errorf("storesqlite: put credential: %w", err)
	}
	return nil
}

// Get implements sca.IssuanceStore.
func (s *SCAIssuanceStore) Get(ctx context.Context, jti string) (sca.IssuedCredential, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT jti, issuer, subject, level, subject_type, jwt, status_list_id, status_list_index, issued_at, expires
FROM credentials WHERE jti = ?`, jti)
	var (
		c        sca.IssuedCredential
		subjType string
		idx      int64
		issuedAt int64
		expires  int64
	)
	if err := row.Scan(&c.JTI, &c.Issuer, &c.Subject, &c.Level, &subjType, &c.JWT,
		&c.StatusListID, &idx, &issuedAt, &expires); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return sca.IssuedCredential{}, sca.ErrJTINotFound
		}
		return sca.IssuedCredential{}, fmt.Errorf("storesqlite: get credential: %w", err)
	}
	c.SubjectType = vc.SubjectType(subjType)
	c.StatusListIndex = uint64(idx)
	c.IssuedAt = time.Unix(issuedAt, 0).UTC()
	c.Expires = time.Unix(expires, 0).UTC()
	return c, nil
}

// SCARevocationStore implements sca.RevocationStore against SQLite with
// rolling status-list semantics: AssignIndex assigns into the active list
// until that list fills, then allocates a new list (`<baseID>-2`,
// `<baseID>-3`, …) and continues there. Old lists remain readable so
// previously-issued credentials' status URLs stay resolvable indefinitely.
type SCARevocationStore struct {
	db       *sql.DB
	baseID   string
	capacity uint64
}

// NewSCARevocationStore returns a rotating RevocationStore backed by db.
// baseID is the first list's ID; subsequent lists are named `<baseID>-2`,
// `<baseID>-3`, … Default capacity is 131072 bits.
func NewSCARevocationStore(db *sql.DB, baseID string, size uint64) (*SCARevocationStore, error) {
	if size == 0 {
		size = 131_072
	}
	return &SCARevocationStore{db: db, baseID: baseID, capacity: size}, nil
}

func (s *SCARevocationStore) listOrdinalName(i int) string {
	if i == 0 {
		return s.baseID
	}
	return fmt.Sprintf("%s-%d", s.baseID, i+1)
}

// AssignIndex implements sca.RevocationStore. The transaction picks the
// oldest non-full list (if any) and bumps its next_index; otherwise it
// inserts a fresh list and returns index 0.
func (s *SCARevocationStore) AssignIndex(ctx context.Context) (string, uint64, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return "", 0, err
	}
	defer tx.Rollback() //nolint:errcheck

	var (
		listID string
		idx    int64
	)
	err = tx.QueryRowContext(ctx, `
SELECT list_id, next_index FROM status_lists
WHERE next_index < size
ORDER BY rowid
LIMIT 1`).Scan(&listID, &idx)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		var count int
		if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM status_lists`).Scan(&count); err != nil {
			return "", 0, fmt.Errorf("storesqlite: count lists: %w", err)
		}
		listID = s.listOrdinalName(count)
		if _, err := tx.ExecContext(ctx, `
INSERT INTO status_lists (list_id, bits, next_index, size)
VALUES (?, ?, 1, ?)`, listID, newEmptyBits(s.capacity), int64(s.capacity)); err != nil {
			return "", 0, fmt.Errorf("storesqlite: insert new list: %w", err)
		}
		if err := tx.Commit(); err != nil {
			return "", 0, fmt.Errorf("storesqlite: commit: %w", err)
		}
		return listID, 0, nil
	case err != nil:
		return "", 0, fmt.Errorf("storesqlite: find active list: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
UPDATE status_lists SET next_index = next_index + 1 WHERE list_id = ?`, listID); err != nil {
		return "", 0, fmt.Errorf("storesqlite: bump next_index: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return "", 0, fmt.Errorf("storesqlite: commit: %w", err)
	}
	return listID, uint64(idx), nil
}

// Revoke implements sca.RevocationStore.
func (s *SCARevocationStore) Revoke(ctx context.Context, listID string, index uint64) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback() //nolint:errcheck
	var bits []byte
	err = tx.QueryRowContext(ctx, `SELECT bits FROM status_lists WHERE list_id = ?`, listID).Scan(&bits)
	if errors.Is(err, sql.ErrNoRows) {
		return sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	if err != nil {
		return fmt.Errorf("storesqlite: read bits: %w", err)
	}
	if index >= uint64(len(bits))*8 {
		return fmt.Errorf("storesqlite: index %d out of range", index)
	}
	bits[index/8] |= 1 << (7 - index%8)
	if _, err := tx.ExecContext(ctx, `UPDATE status_lists SET bits = ? WHERE list_id = ?`, bits, listID); err != nil {
		return fmt.Errorf("storesqlite: write bits: %w", err)
	}
	return tx.Commit()
}

// Snapshot implements sca.RevocationStore.
func (s *SCARevocationStore) Snapshot(ctx context.Context, listID string) (*vc.StatusList, error) {
	var bits []byte
	err := s.db.QueryRowContext(ctx, `SELECT bits FROM status_lists WHERE list_id = ?`, listID).Scan(&bits)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, sca.New(404, sca.CodeRevoked, "unknown listID")
	}
	if err != nil {
		return nil, fmt.Errorf("storesqlite: read bits: %w", err)
	}
	// Reconstruct the StatusList from raw bits.
	tmp := vc.NewStatusList(uint64(len(bits)) * 8)
	for i := uint64(0); i < uint64(len(bits))*8; i++ {
		if bits[i/8]&(1<<(7-i%8)) != 0 {
			if err := tmp.Set(i, true); err != nil {
				return nil, err
			}
		}
	}
	return tmp, nil
}

func newEmptyBits(size uint64) []byte {
	return make([]byte, (size+7)/8)
}

func nullableUnix(t time.Time) sql.NullInt64 {
	if t.IsZero() {
		return sql.NullInt64{}
	}
	return sql.NullInt64{Int64: t.Unix(), Valid: true}
}

// rowsAffectedOrErr maps an UPDATE's RowsAffected into a state-mismatch
// vs not-found error, by re-checking whether the row exists.
func rowsAffectedOrErr(ctx context.Context, db *sql.DB, res sql.Result, stateErr, notFoundErr error, table, id string) error {
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	var found int
	if err := db.QueryRowContext(ctx, `SELECT 1 FROM `+table+` WHERE id = ?`, id).Scan(&found); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return notFoundErr
		}
		return err
	}
	return stateErr
}
