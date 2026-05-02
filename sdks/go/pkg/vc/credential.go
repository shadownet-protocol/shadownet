// SPDX-License-Identifier: MIT

package vc

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet-go/pkg/crypto"
	"github.com/shadownet-protocol/shadownet-go/pkg/did"
)

// Version is the protocol version stamped on every artifact this package
// emits, per RFC-0001 §Versioning.
const Version = "0.1"

// Wire-format constants from RFC-0003.
const (
	TypVCJWT = "vc+jwt"
	TypVPJWT = "vp+jwt"

	ContextW3CCredentialsV2 = "https://www.w3.org/ns/credentials/v2"
	ContextShadownetV1      = "https://shadownet.example/contexts/v1"

	CredentialType                 = "VerifiableCredential"
	ShadownetSubjectCredentialType = "ShadownetSubjectCredential"
	PresentationType               = "VerifiablePresentation"

	StatusEntryTypeBitstring = "BitstringStatusListEntry"
)

// Levels defined by RFC-0003 §Levels.
const (
	LevelL1 = "urn:shadownet:level:L1"
	LevelL2 = "urn:shadownet:level:L2"
	LevelL3 = "urn:shadownet:level:L3"
	LevelO1 = "urn:shadownet:level:O1"
)

// SubjectType is "person" or "organization".
type SubjectType string

// SubjectType values defined by RFC-0003.
const (
	SubjectPerson       SubjectType = "person"
	SubjectOrganization SubjectType = "organization"
)

// MaxCredentialLifetime is the SHOULD-cap from RFC-0003 §Lifetimes-and-freshness.
const MaxCredentialLifetime = 90 * 24 * time.Hour

// Credential is the structured view of a Shadownet subject credential.
type Credential struct {
	Issuer      string // did:web:... (the SCA)
	Subject     string // did:key:... or did:web:... (the holder)
	JTI         string // urn:uuid:...
	IssuedAt    time.Time
	Expires     time.Time
	Level       string // urn:shadownet:level:L1..., or any URI per §Levels
	SubjectType SubjectType
	Status      *Status // optional revocation pointer
}

// Status is the credentialStatus pointer to an entry in a BitstringStatusList.
type Status struct {
	StatusListIndex      uint64
	StatusListCredential string
}

// IssueOptions controls credential issuance. The IssuerKeyID is the JWS "kid"
// header — typically the issuer DID with a key fragment, e.g.
// "did:web:sca.shadownet.example#k1".
type IssueOptions struct {
	IssuerKeyID string
}

// IssueCredential signs a credential JWT for the given Credential value using
// kp (the issuer's keypair). The Issuer field MUST be the same DID whose key
// signs the JWT.
func IssueCredential(kp crypto.KeyPair, c Credential, opts IssueOptions) (string, error) {
	if c.Issuer == "" {
		return "", errors.New("vc: credential.Issuer required")
	}
	if c.Subject == "" {
		return "", errors.New("vc: credential.Subject required")
	}
	if c.JTI == "" {
		return "", errors.New("vc: credential.JTI required")
	}
	if c.Level == "" {
		return "", errors.New("vc: credential.Level required")
	}
	if c.SubjectType != SubjectPerson && c.SubjectType != SubjectOrganization {
		return "", fmt.Errorf("vc: credential.SubjectType = %q, must be person or organization", c.SubjectType)
	}
	if c.SubjectType == SubjectOrganization && !strings.HasPrefix(c.Subject, "did:web:") {
		return "", errors.New("vc: organization credentials require a did:web subject (RFC-0003)")
	}
	if c.IssuedAt.IsZero() || c.Expires.IsZero() {
		return "", errors.New("vc: IssuedAt and Expires required")
	}
	if !c.Expires.After(c.IssuedAt) {
		return "", errors.New("vc: Expires must be after IssuedAt")
	}
	if c.Expires.Sub(c.IssuedAt) > MaxCredentialLifetime {
		return "", fmt.Errorf("vc: credential lifetime %v exceeds RFC-0003 maximum %v", c.Expires.Sub(c.IssuedAt), MaxCredentialLifetime)
	}
	if opts.IssuerKeyID == "" {
		return "", errors.New("vc: IssueOptions.IssuerKeyID required")
	}
	issDID, _ := did.SplitDIDURL(opts.IssuerKeyID)
	if issDID != c.Issuer {
		return "", fmt.Errorf("vc: kid DID %q must match credential.Issuer %q", issDID, c.Issuer)
	}

	claims := wireCredential{
		Iss:     c.Issuer,
		Sub:     c.Subject,
		Iat:     c.IssuedAt.Unix(),
		Exp:     c.Expires.Unix(),
		Jti:     c.JTI,
		Version: Version,
		VC: wireVCBody{
			Context: []string{ContextW3CCredentialsV2, ContextShadownetV1},
			Type:    []string{CredentialType, ShadownetSubjectCredentialType},
			CredentialSubject: wireSubject{
				ID:          c.Subject,
				Level:       c.Level,
				SubjectType: string(c.SubjectType),
			},
		},
	}
	if c.Status != nil {
		claims.VC.CredentialStatus = &wireStatus{
			Type:                 StatusEntryTypeBitstring,
			StatusListIndex:      strconv.FormatUint(c.Status.StatusListIndex, 10),
			StatusListCredential: c.Status.StatusListCredential,
		}
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{
		KeyID: opts.IssuerKeyID,
		Type:  TypVCJWT,
	})
}

// VerifyCredential parses a credential JWT, verifies its signature against the
// issuer's DID document, validates the structural shape from RFC-0003, and
// returns the structured view.
//
// VerifyCredential does NOT consult any trust store or status list — those
// are higher-level concerns handled by Verifier.
func VerifyCredential(ctx context.Context, r did.Resolver, compact string, now time.Time) (*Credential, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, err
	}
	if hdr.Typ != TypVCJWT {
		return nil, fmt.Errorf("vc: typ = %q, want %q", hdr.Typ, TypVCJWT)
	}
	if hdr.Kid == "" {
		return nil, errors.New("vc: JWS missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("vc: resolve issuer key: %w", err)
	}
	var w wireCredential
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, err
	}
	return validateCredential(&w, hdr.Kid, now)
}

func validateCredential(w *wireCredential, kid string, now time.Time) (*Credential, error) {
	if w.Version != Version {
		return nil, fmt.Errorf("vc: shadownet:v = %q, want %q", w.Version, Version)
	}
	if w.Iss == "" || w.Sub == "" || w.Jti == "" {
		return nil, errors.New("vc: missing iss/sub/jti")
	}
	issDID, _ := did.SplitDIDURL(kid)
	if issDID != w.Iss {
		return nil, fmt.Errorf("vc: kid DID %q does not match iss %q", issDID, w.Iss)
	}
	if w.Iat == 0 || w.Exp == 0 {
		return nil, errors.New("vc: missing iat or exp")
	}
	if w.Exp <= w.Iat {
		return nil, errors.New("vc: exp must be after iat")
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, fmt.Errorf("vc: credential expired at %d (now %d)", w.Exp, now.Unix())
	}
	if w.VC.CredentialSubject.ID == "" || w.VC.CredentialSubject.Level == "" || w.VC.CredentialSubject.SubjectType == "" {
		return nil, errors.New("vc: credentialSubject missing required fields")
	}
	if w.VC.CredentialSubject.ID != w.Sub {
		return nil, fmt.Errorf("vc: credentialSubject.id %q does not match sub %q", w.VC.CredentialSubject.ID, w.Sub)
	}
	subjType := SubjectType(w.VC.CredentialSubject.SubjectType)
	if subjType != SubjectPerson && subjType != SubjectOrganization {
		return nil, fmt.Errorf("vc: subjectType = %q, must be person or organization", subjType)
	}
	if subjType == SubjectOrganization && !strings.HasPrefix(w.Sub, "did:web:") {
		return nil, errors.New("vc: organization credentials must have a did:web subject (RFC-0003)")
	}
	if !containsString(w.VC.Context, ContextW3CCredentialsV2) {
		return nil, fmt.Errorf("vc: @context missing %q", ContextW3CCredentialsV2)
	}
	if !containsString(w.VC.Type, CredentialType) {
		return nil, fmt.Errorf("vc: type missing %q", CredentialType)
	}
	out := &Credential{
		Issuer:      w.Iss,
		Subject:     w.Sub,
		JTI:         w.Jti,
		IssuedAt:    time.Unix(w.Iat, 0).UTC(),
		Expires:     time.Unix(w.Exp, 0).UTC(),
		Level:       w.VC.CredentialSubject.Level,
		SubjectType: subjType,
	}
	if w.VC.CredentialStatus != nil {
		if w.VC.CredentialStatus.Type != StatusEntryTypeBitstring {
			return nil, fmt.Errorf("vc: credentialStatus.type = %q, want %q", w.VC.CredentialStatus.Type, StatusEntryTypeBitstring)
		}
		idx, err := strconv.ParseUint(w.VC.CredentialStatus.StatusListIndex, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("vc: parse statusListIndex: %w", err)
		}
		if w.VC.CredentialStatus.StatusListCredential == "" {
			return nil, errors.New("vc: credentialStatus.statusListCredential required")
		}
		out.Status = &Status{
			StatusListIndex:      idx,
			StatusListCredential: w.VC.CredentialStatus.StatusListCredential,
		}
	}
	return out, nil
}

func containsString(xs []string, want string) bool {
	for _, x := range xs {
		if x == want {
			return true
		}
	}
	return false
}

// wireCredential is the on-wire JSON shape of a VC-JWT payload.
type wireCredential struct {
	Iss     string     `json:"iss"`
	Sub     string     `json:"sub"`
	Iat     int64      `json:"iat"`
	Exp     int64      `json:"exp"`
	Jti     string     `json:"jti"`
	Version string     `json:"shadownet:v"`
	VC      wireVCBody `json:"vc"`
}

type wireVCBody struct {
	Context           []string    `json:"@context"`
	Type              []string    `json:"type"`
	CredentialSubject wireSubject `json:"credentialSubject"`
	CredentialStatus  *wireStatus `json:"credentialStatus,omitempty"`
}

type wireSubject struct {
	ID          string `json:"id"`
	Level       string `json:"level"`
	SubjectType string `json:"subjectType"`
}

type wireStatus struct {
	Type                 string `json:"type"`
	StatusListIndex      string `json:"statusListIndex"`
	StatusListCredential string `json:"statusListCredential"`
}
