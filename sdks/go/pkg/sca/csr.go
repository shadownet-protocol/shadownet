// SPDX-License-Identifier: MIT

package sca

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/did"
	"github.com/shadownet-protocol/shadownet/sdks/go/pkg/vc"
)

// MaxCSRLifetime caps the (exp - iat) window of a CSR JWT. RFC-0004 does not
// fix this bound; we apply 10 minutes — generous for real flows, tight enough
// to limit replay surface.
const MaxCSRLifetime = 10 * time.Minute

// CSR is the structured view of a Certificate Signing Request JWT, signed by
// the subject and submitted to /issuance per RFC-0004.
type CSR struct {
	Subject     string // == iss
	Audience    string // SCA DID
	Level       string
	SubjectType vc.SubjectType
	IssuedAt    time.Time
	Expires     time.Time
}

// VerifyCSR parses and verifies a CSR JWT against its declared issuer DID.
// The returned CSR is ready for the Issuer to compare against the bound
// proof session.
func VerifyCSR(ctx context.Context, r did.Resolver, compact, expectedAud string, now time.Time) (*CSR, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "parse CSR header").wrap(err)
	}
	if hdr.Kid == "" {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR kid required")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "resolve subject key").wrap(err)
	}
	var w wireCSR
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "verify CSR signature").wrap(err)
	}
	if w.Iss == "" || w.Aud == "" {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR missing iss/aud")
	}
	issDID, _ := did.SplitDIDURL(hdr.Kid)
	if issDID != w.Iss {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR kid DID does not match iss")
	}
	if expectedAud != "" && w.Aud != expectedAud {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, fmt.Sprintf("CSR aud %q does not match SCA DID %q", w.Aud, expectedAud))
	}
	if w.Iat == 0 || w.Exp == 0 || w.Exp <= w.Iat {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR iat/exp invalid")
	}
	if time.Duration(w.Exp-w.Iat)*time.Second > MaxCSRLifetime {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR lifetime exceeds 10 minutes")
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR expired")
	}
	if w.Request.Level == "" || w.Request.SubjectType == "" {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR.request missing level/subjectType")
	}
	st := vc.SubjectType(w.Request.SubjectType)
	if st != vc.SubjectPerson && st != vc.SubjectOrganization {
		return nil, New(http.StatusBadRequest, CodeCSRInvalid, "CSR.request.subjectType invalid")
	}
	return &CSR{
		Subject:     w.Iss,
		Audience:    w.Aud,
		Level:       w.Request.Level,
		SubjectType: st,
		IssuedAt:    time.Unix(w.Iat, 0).UTC(),
		Expires:     time.Unix(w.Exp, 0).UTC(),
	}, nil
}

// IssueCSR is a helper for clients that want to mint a CSR JWT. It is exported
// because cmd/shadownet (CLI) and downstream Go agents will use it.
func IssueCSR(kp crypto.KeyPair, subject, subjectKeyID, audience, level string, subjectType vc.SubjectType, iat, exp time.Time) (string, error) {
	if subject == "" || subjectKeyID == "" || audience == "" {
		return "", errors.New("sca: subject, subjectKeyID, audience required")
	}
	if !exp.After(iat) {
		return "", errors.New("sca: exp must be after iat")
	}
	if exp.Sub(iat) > MaxCSRLifetime {
		return "", fmt.Errorf("sca: CSR lifetime %v exceeds %v", exp.Sub(iat), MaxCSRLifetime)
	}
	return crypto.SignJWT(kp.Private, wireCSR{
		Iss:     subject,
		Aud:     audience,
		Iat:     iat.Unix(),
		Exp:     exp.Unix(),
		Version: vc.Version,
		Request: csrRequest{
			Level:       level,
			SubjectType: string(subjectType),
		},
	}, crypto.SignerOptions{KeyID: subjectKeyID, Type: "JWT"})
}

type wireCSR struct {
	Iss     string     `json:"iss"`
	Aud     string     `json:"aud"`
	Iat     int64      `json:"iat"`
	Exp     int64      `json:"exp"`
	Version string     `json:"shadownet:v"`
	Request csrRequest `json:"request"`
}

type csrRequest struct {
	Level       string `json:"level"`
	SubjectType string `json:"subjectType"`
}

// wrap is a small helper so VerifyCSR can attach causes without restating the
// status/code/detail fields each time.
func (e *Error) wrap(cause error) *Error {
	e.Cause = cause
	return e
}
