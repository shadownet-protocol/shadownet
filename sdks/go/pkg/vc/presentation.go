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

// MaxPresentationLifetime is the cap per RFC-0003 §Presentation: VPs MUST
// have (exp - iat) ≤ 120 s.
const MaxPresentationLifetime = 120 * time.Second

// Presentation is the structured view of a Verifiable Presentation JWT.
type Presentation struct {
	Holder      string
	Audience    string
	Nonce       string
	IssuedAt    time.Time
	Expires     time.Time
	Credentials []string // raw JWTs as carried inside the VP
}

// IssuePresentation signs a VP JWT. holder MUST equal the DID of the public
// key matching kp. credentials are raw JWT strings (typically a credential
// JWT plus an optional freshness JWT).
func IssuePresentation(kp crypto.KeyPair, holder, holderKeyID, audience, nonce string, credentials []string, iat, exp time.Time) (string, error) {
	if holder == "" || holderKeyID == "" {
		return "", errors.New("vc: holder and holderKeyID required")
	}
	if audience == "" {
		return "", errors.New("vc: audience required")
	}
	if nonce == "" {
		return "", errors.New("vc: nonce required")
	}
	if len(credentials) == 0 {
		return "", errors.New("vc: at least one credential is required in a VP")
	}
	if iat.IsZero() || exp.IsZero() {
		return "", errors.New("vc: iat and exp required")
	}
	if !exp.After(iat) {
		return "", errors.New("vc: exp must be after iat")
	}
	if exp.Sub(iat) > MaxPresentationLifetime {
		return "", fmt.Errorf("vc: presentation lifetime %v exceeds RFC-0003 maximum %v", exp.Sub(iat), MaxPresentationLifetime)
	}
	holderDID, _ := did.SplitDIDURL(holderKeyID)
	if holderDID != holder {
		return "", fmt.Errorf("vc: kid DID %q must match holder %q", holderDID, holder)
	}
	claims := wirePresentation{
		Iss:   holder,
		Aud:   audience,
		Iat:   iat.Unix(),
		Exp:   exp.Unix(),
		Nonce: nonce,
		VP: wireVPBody{
			Context:               []string{ContextW3CCredentialsV2},
			Type:                  []string{PresentationType},
			VerifiableCredentials: credentials,
		},
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{
		KeyID: holderKeyID,
		Type:  TypVPJWT,
	})
}

// VerifyPresentation parses a VP JWT, resolves the holder's key, verifies the
// signature, asserts aud + nonce + lifetime constraints, and returns the
// structured view. Embedded credentials are returned as raw JWT strings —
// callers verify them separately (typically via Verifier.VerifyPresentation).
func VerifyPresentation(ctx context.Context, r did.Resolver, compact, expectedAud, expectedNonce string, now time.Time) (*Presentation, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, err
	}
	if hdr.Typ != TypVPJWT {
		return nil, fmt.Errorf("vc: typ = %q, want %q", hdr.Typ, TypVPJWT)
	}
	if hdr.Kid == "" {
		return nil, errors.New("vc: VP missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("vc: resolve holder key: %w", err)
	}
	var w wirePresentation
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, err
	}
	if w.Iss == "" || w.Aud == "" || w.Nonce == "" {
		return nil, errors.New("vc: presentation missing iss/aud/nonce")
	}
	holderDID, _ := did.SplitDIDURL(hdr.Kid)
	if holderDID != w.Iss {
		return nil, fmt.Errorf("vc: kid DID %q does not match VP iss %q", holderDID, w.Iss)
	}
	if expectedAud != "" && w.Aud != expectedAud {
		return nil, fmt.Errorf("vc: aud %q does not match expected %q", w.Aud, expectedAud)
	}
	if expectedNonce != "" && w.Nonce != expectedNonce {
		return nil, errors.New("vc: nonce mismatch")
	}
	if w.Exp <= w.Iat {
		return nil, errors.New("vc: exp must be after iat")
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, errors.New("vc: presentation expired")
	}
	if !containsString(w.VP.Context, ContextW3CCredentialsV2) {
		return nil, fmt.Errorf("vc: vp.@context missing %q", ContextW3CCredentialsV2)
	}
	if !containsString(w.VP.Type, PresentationType) {
		return nil, fmt.Errorf("vc: vp.type missing %q", PresentationType)
	}
	if len(w.VP.VerifiableCredentials) == 0 {
		return nil, errors.New("vc: presentation contains no credentials")
	}
	return &Presentation{
		Holder:      w.Iss,
		Audience:    w.Aud,
		Nonce:       w.Nonce,
		IssuedAt:    time.Unix(w.Iat, 0).UTC(),
		Expires:     time.Unix(w.Exp, 0).UTC(),
		Credentials: w.VP.VerifiableCredentials,
	}, nil
}

type wirePresentation struct {
	Iss   string     `json:"iss"`
	Aud   string     `json:"aud"`
	Iat   int64      `json:"iat"`
	Exp   int64      `json:"exp"`
	Nonce string     `json:"nonce"`
	VP    wireVPBody `json:"vp"`
}

type wireVPBody struct {
	Context               []string `json:"@context"`
	Type                  []string `json:"type"`
	VerifiableCredentials []string `json:"verifiableCredential"`
}
