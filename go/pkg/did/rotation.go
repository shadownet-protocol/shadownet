// SPDX-License-Identifier: MIT

package did

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/shadownet-protocol/shadownet/go/pkg/crypto"
)

// KeyRotationStatement is the JWT defined in RFC-0002 §Key rotation: a signed
// assertion by the *old* DID that names the new DID it has rotated to.
type KeyRotationStatement struct {
	Issuer    string    // old DID (also the JWT's "iss")
	Subject   string    // new DID (JWT's "sub")
	Purpose   string    // always "key-rotation"
	ValidFrom time.Time // RFC 3339 in JSON; epoch seconds in IssuedAt
	IssuedAt  time.Time
}

type rotationClaims struct {
	Iss       string `json:"iss"`
	Sub       string `json:"sub"`
	Purpose   string `json:"purpose"`
	ValidFrom string `json:"validFrom"`
	Iat       int64  `json:"iat"`
}

// IssueKeyRotation signs a KeyRotationStatement with the old key. kp.Public
// MUST correspond to oldDID.
func IssueKeyRotation(kp crypto.KeyPair, oldDID, newDID string, validFrom, now time.Time) (string, error) {
	if oldDID == "" || newDID == "" {
		return "", errors.New("did: rotation requires both old and new DID")
	}
	if oldDID == newDID {
		return "", errors.New("did: rotation old DID equals new DID")
	}
	claims := rotationClaims{
		Iss:       oldDID,
		Sub:       newDID,
		Purpose:   "key-rotation",
		ValidFrom: validFrom.UTC().Format(time.RFC3339),
		Iat:       now.UTC().Unix(),
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{
		KeyID: oldDID,
		Type:  "JWT",
	})
}

// VerifyKeyRotation verifies the JWS using r to look up the old DID's key,
// returns the structured statement, and asserts payload sanity.
//
// VerifyKeyRotation does not enforce that ValidFrom is in the past; callers
// (which know their freshness window) decide whether the rotation is in
// effect for the moment in question.
func VerifyKeyRotation(ctx context.Context, r Resolver, compact string) (KeyRotationStatement, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return KeyRotationStatement{}, err
	}
	if hdr.Kid == "" {
		return KeyRotationStatement{}, errors.New("did: rotation JWS missing kid")
	}
	pub, err := LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return KeyRotationStatement{}, fmt.Errorf("did: resolve old key: %w", err)
	}
	var claims rotationClaims
	if _, err := crypto.VerifyJWT(pub, compact, &claims); err != nil {
		return KeyRotationStatement{}, err
	}
	if claims.Purpose != "key-rotation" {
		return KeyRotationStatement{}, fmt.Errorf("did: rotation purpose = %q, want key-rotation", claims.Purpose)
	}
	if claims.Iss == "" || claims.Sub == "" {
		return KeyRotationStatement{}, errors.New("did: rotation missing iss or sub")
	}
	if claims.Iss == claims.Sub {
		return KeyRotationStatement{}, errors.New("did: rotation iss equals sub")
	}
	// Cross-check: the resolved key must be the iss DID (so attackers cannot
	// sign with one DID's key while claiming iss is another DID).
	issDID, _ := SplitDIDURL(claims.Iss)
	kidDID, _ := SplitDIDURL(hdr.Kid)
	if issDID != kidDID {
		return KeyRotationStatement{}, fmt.Errorf("did: rotation iss %q does not match kid DID %q", issDID, kidDID)
	}
	validFrom, err := time.Parse(time.RFC3339, claims.ValidFrom)
	if err != nil {
		return KeyRotationStatement{}, fmt.Errorf("did: parse validFrom: %w", err)
	}
	return KeyRotationStatement{
		Issuer:    claims.Iss,
		Subject:   claims.Sub,
		Purpose:   claims.Purpose,
		ValidFrom: validFrom,
		IssuedAt:  time.Unix(claims.Iat, 0).UTC(),
	}, nil
}
