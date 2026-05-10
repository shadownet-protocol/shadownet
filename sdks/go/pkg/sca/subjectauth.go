// SPDX-License-Identifier: MIT

package sca

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
)

// MaxSubjectAuthLifetime is the (exp - iat) cap from RFC-0004 §Common.
const MaxSubjectAuthLifetime = 60 * time.Second

// SubjectAuth is the structured view of the Bearer JWT a subject sends with
// every authenticated SCA request.
type SubjectAuth struct {
	Subject  string
	Audience string
	JTI      string
	IssuedAt time.Time
	Expires  time.Time
}

// VerifySubjectAuth parses and verifies the Authorization Bearer JWT.
//
// expectedAud is the SCA's own DID (RFC-0004 requires `aud` to match).
func VerifySubjectAuth(ctx context.Context, r did.Resolver, headerValue, expectedAud string, now time.Time) (*SubjectAuth, error) {
	const prefix = "Bearer "
	if !strings.HasPrefix(headerValue, prefix) {
		return nil, Unauthorized("missing or non-bearer Authorization header", nil)
	}
	compact := strings.TrimSpace(headerValue[len(prefix):])
	if compact == "" {
		return nil, Unauthorized("empty bearer token", nil)
	}
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, Unauthorized("parse subject-auth header", err)
	}
	if hdr.Kid == "" {
		return nil, Unauthorized("subject-auth missing kid", nil)
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, Unauthorized("resolve subject key", err)
	}
	var w wireSubjectAuth
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, Unauthorized("verify subject-auth signature", err)
	}
	if w.Iss == "" || w.Aud == "" {
		return nil, Unauthorized("subject-auth missing iss/aud", nil)
	}
	issDID, _ := did.SplitDIDURL(hdr.Kid)
	if issDID != w.Iss {
		return nil, Unauthorized("subject-auth kid does not match iss", nil)
	}
	if expectedAud != "" && w.Aud != expectedAud {
		return nil, Unauthorized(fmt.Sprintf("subject-auth aud %q does not match SCA DID %q", w.Aud, expectedAud), nil)
	}
	if w.Iat == 0 || w.Exp == 0 || w.Exp <= w.Iat {
		return nil, Unauthorized("subject-auth iat/exp invalid", nil)
	}
	if time.Duration(w.Exp-w.Iat)*time.Second > MaxSubjectAuthLifetime {
		return nil, Unauthorized("subject-auth lifetime exceeds 60 s", nil)
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, Unauthorized("subject-auth expired", nil)
	}
	if w.Purpose != "sca-request" {
		return nil, Unauthorized(fmt.Sprintf("subject-auth purpose %q != sca-request", w.Purpose), nil)
	}
	return &SubjectAuth{
		Subject:  w.Iss,
		Audience: w.Aud,
		JTI:      w.JTI,
		IssuedAt: time.Unix(w.Iat, 0).UTC(),
		Expires:  time.Unix(w.Exp, 0).UTC(),
	}, nil
}

// IssueSubjectAuth is a helper for clients (CLI, agent SDK consumers).
func IssueSubjectAuth(kp crypto.KeyPair, subject, subjectKeyID, audience, jti string, iat, exp time.Time) (string, error) {
	if subject == "" || subjectKeyID == "" || audience == "" || jti == "" {
		return "", fmt.Errorf("sca: subject, subjectKeyID, audience, jti required")
	}
	if !exp.After(iat) {
		return "", fmt.Errorf("sca: exp must be after iat")
	}
	if exp.Sub(iat) > MaxSubjectAuthLifetime {
		return "", fmt.Errorf("sca: subject-auth lifetime %v exceeds %v", exp.Sub(iat), MaxSubjectAuthLifetime)
	}
	return crypto.SignJWT(kp.Private, wireSubjectAuth{
		Iss:     subject,
		Aud:     audience,
		Iat:     iat.Unix(),
		Exp:     exp.Unix(),
		JTI:     jti,
		Version: "0.1",
		Purpose: "sca-request",
	}, crypto.SignerOptions{KeyID: subjectKeyID, Type: "JWT"})
}

type wireSubjectAuth struct {
	Iss     string `json:"iss"`
	Aud     string `json:"aud"`
	Iat     int64  `json:"iat"`
	Exp     int64  `json:"exp"`
	JTI     string `json:"jti"`
	Version string `json:"shadownet:v"`
	Purpose string `json:"purpose"`
}
