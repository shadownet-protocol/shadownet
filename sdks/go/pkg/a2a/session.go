// SPDX-License-Identifier: MIT

package a2a

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/did"
)

// MaxSessionTokenLifetime is the cap from RFC-0006 §Session token: a
// session-token JWT MUST have (exp - iat) ≤ 300 s.
const MaxSessionTokenLifetime = 300 * time.Second

// SessionToken is the structured view of the Bearer JWT each A2A request
// carries between Shadows.
type SessionToken struct {
	Issuer   string // caller DID
	Audience string // callee DID (must match server's own DID)
	JTI      string
	IssuedAt time.Time
	Expires  time.Time
}

// IssueSessionToken signs a session-token JWT per RFC-0006.
func IssueSessionToken(kp crypto.KeyPair, callerDID, callerKeyID, calleeDID, jti string, iat, exp time.Time) (string, error) {
	if callerDID == "" || callerKeyID == "" || calleeDID == "" || jti == "" {
		return "", errors.New("a2a: callerDID, callerKeyID, calleeDID, jti required")
	}
	if !exp.After(iat) {
		return "", errors.New("a2a: exp must be after iat")
	}
	if exp.Sub(iat) > MaxSessionTokenLifetime {
		return "", fmt.Errorf("a2a: session-token lifetime %v exceeds %v", exp.Sub(iat), MaxSessionTokenLifetime)
	}
	if d, _ := did.SplitDIDURL(callerKeyID); d != callerDID {
		return "", fmt.Errorf("a2a: kid DID %q does not match caller DID %q", d, callerDID)
	}
	return crypto.SignJWT(kp.Private, wireSessionToken{
		Iss: callerDID, Aud: calleeDID, Iat: iat.Unix(), Exp: exp.Unix(),
		JTI: jti, Version: "0.1", Purpose: "a2a-session",
	}, crypto.SignerOptions{KeyID: callerKeyID, Type: "JWT"})
}

// VerifySessionToken parses, verifies, and validates a session-token Bearer
// JWT against the expected callee DID.
func VerifySessionToken(ctx context.Context, r did.Resolver, headerValue, expectedAud string, now time.Time) (*SessionToken, error) {
	const prefix = "Bearer "
	if !strings.HasPrefix(headerValue, prefix) {
		return nil, errors.New("a2a: missing or non-bearer Authorization")
	}
	compact := strings.TrimSpace(headerValue[len(prefix):])
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, fmt.Errorf("a2a: parse session token header: %w", err)
	}
	if hdr.Kid == "" {
		return nil, errors.New("a2a: session token missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("a2a: resolve caller key: %w", err)
	}
	var w wireSessionToken
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, fmt.Errorf("a2a: verify session token: %w", err)
	}
	if w.Iss == "" || w.Aud == "" {
		return nil, errors.New("a2a: session token missing iss/aud")
	}
	if d, _ := did.SplitDIDURL(hdr.Kid); d != w.Iss {
		return nil, errors.New("a2a: session token kid DID does not match iss")
	}
	if expectedAud != "" && w.Aud != expectedAud {
		return nil, fmt.Errorf("a2a: session token aud %q does not match callee DID %q", w.Aud, expectedAud)
	}
	if w.Iat == 0 || w.Exp == 0 || w.Exp <= w.Iat {
		return nil, errors.New("a2a: session token iat/exp invalid")
	}
	if time.Duration(w.Exp-w.Iat)*time.Second > MaxSessionTokenLifetime {
		return nil, errors.New("a2a: session token lifetime exceeds 300 s")
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, errors.New("a2a: session token expired")
	}
	if w.Purpose != "a2a-session" {
		return nil, fmt.Errorf("a2a: session token purpose %q != a2a-session", w.Purpose)
	}
	return &SessionToken{
		Issuer:   w.Iss,
		Audience: w.Aud,
		JTI:      w.JTI,
		IssuedAt: time.Unix(w.Iat, 0).UTC(),
		Expires:  time.Unix(w.Exp, 0).UTC(),
	}, nil
}

type wireSessionToken struct {
	Iss     string `json:"iss"`
	Aud     string `json:"aud"`
	Iat     int64  `json:"iat"`
	Exp     int64  `json:"exp"`
	JTI     string `json:"jti"`
	Version string `json:"shadownet:v"`
	Purpose string `json:"purpose"`
}
