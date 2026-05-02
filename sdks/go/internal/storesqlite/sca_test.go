// SPDX-License-Identifier: MIT

package storesqlite

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/sca"
)

func openTestDB(t *testing.T) (*SCASessionStore, *SCAIssuanceStore, *SCARevocationStore) {
	t.Helper()
	db, err := Open(":memory:")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	rev, err := NewSCARevocationStore(db, sca.DefaultListID, 1024)
	if err != nil {
		t.Fatalf("NewSCARevocationStore: %v", err)
	}
	return NewSCASessionStore(db), NewSCAIssuanceStore(db), rev
}

func TestSQLiteSessionLifecycle(t *testing.T) {
	sessions, _, _ := openTestDB(t)
	ctx := context.Background()

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	s := sca.Session{
		ID: "ses-1", Subject: "did:key:zSubject", Level: "urn:shadownet:level:L1",
		Method: "instant-approval", State: sca.StatePending,
		CreatedAt: now, ExpiresAt: now.Add(time.Hour),
	}
	if err := sessions.Put(ctx, s); err != nil {
		t.Fatalf("Put: %v", err)
	}

	got, err := sessions.Get(ctx, "ses-1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.Subject != s.Subject || got.State != sca.StatePending {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}

	if err := sessions.MarkReady(ctx, "ses-1", now.Add(time.Minute)); err != nil {
		t.Fatalf("MarkReady: %v", err)
	}
	got, _ = sessions.Get(ctx, "ses-1")
	if got.State != sca.StateReady {
		t.Fatalf("state = %q, want ready", got.State)
	}

	// MarkReady again must fail (state mismatch).
	if err := sessions.MarkReady(ctx, "ses-1", now); !errors.Is(err, sca.ErrSessionState) {
		t.Fatalf("expected ErrSessionState, got %v", err)
	}

	if err := sessions.Consume(ctx, "ses-1"); err != nil {
		t.Fatalf("Consume: %v", err)
	}
	if err := sessions.Consume(ctx, "ses-1"); !errors.Is(err, sca.ErrSessionState) {
		t.Fatalf("second Consume should fail with ErrSessionState, got %v", err)
	}

	if _, err := sessions.Get(ctx, "missing"); !errors.Is(err, sca.ErrSessionNotFound) {
		t.Fatalf("expected ErrSessionNotFound, got %v", err)
	}
}

func TestSQLiteRevocation(t *testing.T) {
	_, _, rev := openTestDB(t)
	ctx := context.Background()

	listID, idx0, err := rev.AssignIndex(ctx)
	if err != nil {
		t.Fatalf("AssignIndex: %v", err)
	}
	listID2, idx1, _ := rev.AssignIndex(ctx)
	if listID != listID2 {
		t.Fatalf("listID inconsistent: %q vs %q", listID, listID2)
	}
	if idx1 != idx0+1 {
		t.Fatalf("indices not sequential: %d, %d", idx0, idx1)
	}

	if err := rev.Revoke(ctx, listID, idx0); err != nil {
		t.Fatalf("Revoke: %v", err)
	}

	list, err := rev.Snapshot(ctx, listID)
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	v, _ := list.Get(idx0)
	if !v {
		t.Fatalf("expected revoked at %d", idx0)
	}
	v, _ = list.Get(idx1)
	if v {
		t.Fatalf("did not expect revoked at %d", idx1)
	}
}

func TestSQLiteIssuanceRoundtrip(t *testing.T) {
	_, issuance, _ := openTestDB(t)
	ctx := context.Background()

	now := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	c := sca.IssuedCredential{
		JTI: "urn:uuid:1", Issuer: "did:web:sca", Subject: "did:key:zS",
		Level: "urn:shadownet:level:L1", SubjectType: "person",
		JWT: "eyJ.example", StatusListID: "main", StatusListIndex: 7,
		IssuedAt: now, Expires: now.Add(48 * time.Hour),
	}
	if err := issuance.Put(ctx, c); err != nil {
		t.Fatalf("Put: %v", err)
	}
	got, err := issuance.Get(ctx, "urn:uuid:1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.JWT != c.JWT || got.StatusListIndex != c.StatusListIndex {
		t.Fatalf("roundtrip mismatch: %+v", got)
	}
	if _, err := issuance.Get(ctx, "missing"); !errors.Is(err, sca.ErrJTINotFound) {
		t.Fatalf("expected ErrJTINotFound, got %v", err)
	}
}
