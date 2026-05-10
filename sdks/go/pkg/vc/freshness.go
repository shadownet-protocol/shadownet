// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
)

// FreshnessVersion is the value of the "shadownet:freshness" claim per
// RFC-0003 §Lifetimes-and-freshness.
const FreshnessVersion = "v1"

// MaxFreshnessLifetime is the SHOULD-cap from RFC-0003: a freshness proof's
// (exp - iat) MUST be within the verifier's freshness window. The default
// window per RFC-0003 is 24 hours.
const MaxFreshnessLifetime = 24 * time.Hour

// Freshness is the structured view of a freshness-proof JWT.
type Freshness struct {
	Issuer        string // SCA DID
	CredentialJTI string // jti of the credential being attested
	IssuedAt      time.Time
	Expires       time.Time
}

// IssueFreshness signs a freshness proof for credentialJTI. The IssuerKeyID
// is the JWS "kid" header — typically the SCA's DID with a key fragment.
func IssueFreshness(kp crypto.KeyPair, issuer, issuerKeyID, credentialJTI string, iat, exp time.Time) (string, error) {
	if issuer == "" || issuerKeyID == "" || credentialJTI == "" {
		return "", errors.New("vc: issuer, issuerKeyID, credentialJTI required")
	}
	if iat.IsZero() || exp.IsZero() {
		return "", errors.New("vc: iat and exp required")
	}
	if !exp.After(iat) {
		return "", errors.New("vc: exp must be after iat")
	}
	if exp.Sub(iat) > MaxFreshnessLifetime {
		return "", fmt.Errorf("vc: freshness lifetime %v exceeds RFC-0003 maximum %v", exp.Sub(iat), MaxFreshnessLifetime)
	}
	issDID, _ := did.SplitDIDURL(issuerKeyID)
	if issDID != issuer {
		return "", fmt.Errorf("vc: kid DID %q must match issuer %q", issDID, issuer)
	}
	claims := wireFreshness{
		Iss:       issuer,
		Sub:       credentialJTI,
		Iat:       iat.Unix(),
		Exp:       exp.Unix(),
		Freshness: FreshnessVersion,
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{
		KeyID: issuerKeyID,
		Type:  "JWT",
	})
}

// VerifyFreshness parses a freshness JWT, verifies it against the issuer's
// DID document, and returns the structured view.
func VerifyFreshness(ctx context.Context, r did.Resolver, compact string, now time.Time) (*Freshness, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, err
	}
	if hdr.Kid == "" {
		return nil, errors.New("vc: freshness JWS missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("vc: resolve issuer key: %w", err)
	}
	var w wireFreshness
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, err
	}
	if w.Freshness != FreshnessVersion {
		return nil, fmt.Errorf("vc: shadownet:freshness = %q, want %q", w.Freshness, FreshnessVersion)
	}
	if w.Iss == "" || w.Sub == "" {
		return nil, errors.New("vc: freshness missing iss or sub")
	}
	issDID, _ := did.SplitDIDURL(hdr.Kid)
	if issDID != w.Iss {
		return nil, fmt.Errorf("vc: kid DID %q does not match iss %q", issDID, w.Iss)
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, fmt.Errorf("vc: freshness proof expired at %d (now %d)", w.Exp, now.Unix())
	}
	return &Freshness{
		Issuer:        w.Iss,
		CredentialJTI: w.Sub,
		IssuedAt:      time.Unix(w.Iat, 0).UTC(),
		Expires:       time.Unix(w.Exp, 0).UTC(),
	}, nil
}

type wireFreshness struct {
	Iss       string `json:"iss"`
	Sub       string `json:"sub"`
	Iat       int64  `json:"iat"`
	Exp       int64  `json:"exp"`
	Freshness string `json:"shadownet:freshness"`
}
