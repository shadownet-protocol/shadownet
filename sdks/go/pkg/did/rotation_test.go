// SPDX-License-Identifier: MIT

package did

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
)

func TestRotationIssueVerifyRoundtrip(t *testing.T) {
	oldKP, _ := crypto.Generate()
	newKP, _ := crypto.Generate()
	oldDID, _ := EncodeKey(oldKP.Public)
	newDID, _ := EncodeKey(newKP.Public)

	now := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	jwt, err := IssueKeyRotation(oldKP, oldDID, newDID, now, now)
	if err != nil {
		t.Fatalf("IssueKeyRotation: %v", err)
	}
	stmt, err := VerifyKeyRotation(context.Background(), NewKeyResolver(), jwt)
	if err != nil {
		t.Fatalf("VerifyKeyRotation: %v", err)
	}
	if stmt.Issuer != oldDID || stmt.Subject != newDID {
		t.Fatalf("wrong issuer/subject: %+v", stmt)
	}
	if !stmt.ValidFrom.Equal(now) {
		t.Fatalf("validFrom = %v, want %v", stmt.ValidFrom, now)
	}
}

func TestRotationRejectsSelfReference(t *testing.T) {
	kp, _ := crypto.Generate()
	didStr, _ := EncodeKey(kp.Public)
	now := time.Now().UTC()
	if _, err := IssueKeyRotation(kp, didStr, didStr, now, now); err == nil {
		t.Fatalf("expected error for old==new")
	}
}

func TestRotationVerifyWrongKey(t *testing.T) {
	oldKP, _ := crypto.Generate()
	newKP, _ := crypto.Generate()
	otherKP, _ := crypto.Generate()
	oldDID, _ := EncodeKey(oldKP.Public)
	newDID, _ := EncodeKey(newKP.Public)
	otherDID, _ := EncodeKey(otherKP.Public)

	now := time.Now().UTC()
	// Sign claims that name oldDID as iss but use otherKP for the JWS — and
	// set kid to otherDID, so the signature will verify against the resolved
	// key but the iss/kid mismatch must trip our cross-check.
	bad, err := crypto.SignJWT(otherKP.Private, rotationClaims{
		Iss:       oldDID, // claims oldDID is issuer
		Sub:       newDID,
		Purpose:   "key-rotation",
		ValidFrom: now.Format(time.RFC3339),
		Iat:       now.Unix(),
	}, crypto.SignerOptions{KeyID: otherDID, Type: "JWT"})
	if err != nil {
		t.Fatalf("SignJWT: %v", err)
	}
	_, err = VerifyKeyRotation(context.Background(), NewKeyResolver(), bad)
	if err == nil {
		t.Fatalf("expected error: iss/kid DID mismatch")
	}
	if !strings.Contains(err.Error(), "iss") {
		t.Fatalf("expected iss-mismatch error, got: %v", err)
	}
}
