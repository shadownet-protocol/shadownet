// SPDX-License-Identifier: MIT

package sns

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
	"github.com/shadownet-protocol/shadownet/core/pkg/did"
	"github.com/shadownet-protocol/shadownet/core/pkg/vc"
)

// Version stamped on every wire artifact this package emits.
const Version = "0.1"

// Per RFC-0005 §Caching, ttl bounds.
const (
	MinTTL = 60
	MaxTTL = 86400
)

// MaxLocalLength caps the local part of a Shadowname per RFC-0005 grammar.
const MaxLocalLength = 63

// Record is the data the SNS resolves a Shadowname to.
type Record struct {
	Shadowname  string         // canonical "local@provider"
	DID         string         // subject's DID
	Endpoint    string         // A2A endpoint URL
	PublicKey   crypto.JWK     // subject's Ed25519 public key as a JWK
	SubjectType vc.SubjectType // person | organization
	TTL         int            // seconds
	IssuedAt    time.Time
}

// SignedRecord is the parsed view of a signed SNS record JWT.
type SignedRecord struct {
	Issuer   string // SNS provider DID
	Subject  string // shadowname
	IssuedAt time.Time
	Expires  time.Time
	Record   Record
}

// Shadowname is the parsed form of a "local@provider" string.
type Shadowname struct {
	Local    string
	Provider string
}

// String returns the canonical "local@provider" form.
func (s Shadowname) String() string { return s.Local + "@" + s.Provider }

// ParseShadowname parses and canonicalizes a Shadowname per RFC-0005 grammar.
// The local part is lowercased; provider is preserved.
func ParseShadowname(in string) (Shadowname, error) {
	at := strings.LastIndexByte(in, '@')
	if at <= 0 || at == len(in)-1 {
		return Shadowname{}, fmt.Errorf("sns: invalid shadowname %q (want local@provider)", in)
	}
	local := strings.ToLower(in[:at])
	provider := in[at+1:]
	if l := len(local); l == 0 || l > MaxLocalLength {
		return Shadowname{}, fmt.Errorf("sns: local part length %d not in [1,%d]", l, MaxLocalLength)
	}
	for i := 0; i < len(local); i++ {
		c := local[i]
		switch {
		case c >= 'a' && c <= 'z':
		case c >= '0' && c <= '9':
		case c == '_' || c == '-' || c == '.':
		default:
			return Shadowname{}, fmt.Errorf("sns: local part has invalid character %q", c)
		}
	}
	if provider == "" || strings.ContainsAny(provider, "/?# ") {
		return Shadowname{}, fmt.Errorf("sns: invalid provider %q", provider)
	}
	return Shadowname{Local: local, Provider: provider}, nil
}

// IssueRecord signs a record envelope as an SNS-provider JWT. The provider's
// kid is the JWS "kid" header.
func IssueRecord(kp crypto.KeyPair, providerDID, providerKID string, r Record, iat time.Time) (string, error) {
	if r.Shadowname == "" || r.DID == "" || r.Endpoint == "" {
		return "", errors.New("sns: record requires Shadowname, DID, Endpoint")
	}
	if r.TTL < MinTTL || r.TTL > MaxTTL {
		return "", fmt.Errorf("sns: ttl %d out of [%d,%d]", r.TTL, MinTTL, MaxTTL)
	}
	if r.SubjectType != vc.SubjectPerson && r.SubjectType != vc.SubjectOrganization {
		return "", fmt.Errorf("sns: subjectType = %q", r.SubjectType)
	}
	if iat.IsZero() {
		return "", errors.New("sns: iat required")
	}
	if d, _ := did.SplitDIDURL(providerKID); d != providerDID {
		return "", fmt.Errorf("sns: kid DID %q does not match provider DID %q", d, providerDID)
	}
	exp := iat.Add(time.Duration(r.TTL) * time.Second)
	r.IssuedAt = iat.UTC()
	claims := wireSignedRecord{
		Iss:     providerDID,
		Sub:     r.Shadowname,
		Iat:     iat.Unix(),
		Exp:     exp.Unix(),
		Version: Version,
		Record: wireRecord{
			Shadowname:  r.Shadowname,
			DID:         r.DID,
			Endpoint:    r.Endpoint,
			PublicKey:   r.PublicKey,
			SubjectType: string(r.SubjectType),
			TTL:         r.TTL,
			IssuedAt:    iat.Unix(),
			Version:     Version,
		},
	}
	return crypto.SignJWT(kp.Private, claims, crypto.SignerOptions{KeyID: providerKID, Type: "JWT"})
}

// VerifyRecord parses and verifies an SNS record JWT.
//
// expectedShadowname is the shadowname the caller asked to resolve; the
// returned record's `sub` MUST match (case-insensitively on `local`).
func VerifyRecord(ctx context.Context, r did.Resolver, compact, expectedShadowname string, now time.Time) (*SignedRecord, error) {
	hdr, err := crypto.PeekHeader(compact)
	if err != nil {
		return nil, err
	}
	if hdr.Kid == "" {
		return nil, errors.New("sns: record missing kid")
	}
	pub, err := did.LookupKey(ctx, r, hdr.Kid)
	if err != nil {
		return nil, fmt.Errorf("sns: resolve provider key: %w", err)
	}
	var w wireSignedRecord
	if _, err := crypto.VerifyJWT(pub, compact, &w); err != nil {
		return nil, err
	}
	if w.Version != Version {
		return nil, fmt.Errorf("sns: shadownet:v = %q, want %q", w.Version, Version)
	}
	issDID, _ := did.SplitDIDURL(hdr.Kid)
	if issDID != w.Iss {
		return nil, fmt.Errorf("sns: kid DID %q does not match iss %q", issDID, w.Iss)
	}
	if w.Iat == 0 || w.Exp == 0 || w.Exp <= w.Iat {
		return nil, errors.New("sns: record iat/exp invalid")
	}
	ttlSec := int64(w.Record.TTL)
	if w.Exp-w.Iat != ttlSec {
		return nil, fmt.Errorf("sns: exp-iat (%d) != record.ttl (%d)", w.Exp-w.Iat, ttlSec)
	}
	if !now.IsZero() && now.Unix() >= w.Exp {
		return nil, errors.New("sns: record expired")
	}
	wantedSN, err := ParseShadowname(expectedShadowname)
	if err != nil {
		return nil, err
	}
	gotSN, err := ParseShadowname(w.Sub)
	if err != nil {
		return nil, fmt.Errorf("sns: parse record sub: %w", err)
	}
	if wantedSN.Local != gotSN.Local || wantedSN.Provider != gotSN.Provider {
		return nil, fmt.Errorf("sns: record sub %q does not match requested %q", w.Sub, expectedShadowname)
	}
	if w.Record.Shadowname != w.Sub {
		return nil, fmt.Errorf("sns: record.shadowname %q does not match sub %q", w.Record.Shadowname, w.Sub)
	}
	st := vc.SubjectType(w.Record.SubjectType)
	if st != vc.SubjectPerson && st != vc.SubjectOrganization {
		return nil, fmt.Errorf("sns: record.subjectType invalid: %q", w.Record.SubjectType)
	}
	return &SignedRecord{
		Issuer:   w.Iss,
		Subject:  w.Sub,
		IssuedAt: time.Unix(w.Iat, 0).UTC(),
		Expires:  time.Unix(w.Exp, 0).UTC(),
		Record: Record{
			Shadowname:  w.Record.Shadowname,
			DID:         w.Record.DID,
			Endpoint:    w.Record.Endpoint,
			PublicKey:   w.Record.PublicKey,
			SubjectType: st,
			TTL:         w.Record.TTL,
			IssuedAt:    time.Unix(w.Record.IssuedAt, 0).UTC(),
		},
	}, nil
}

type wireSignedRecord struct {
	Iss     string     `json:"iss"`
	Sub     string     `json:"sub"`
	Iat     int64      `json:"iat"`
	Exp     int64      `json:"exp"`
	Version string     `json:"shadownet:v"`
	Record  wireRecord `json:"record"`
}

type wireRecord struct {
	Shadowname  string     `json:"shadowname"`
	DID         string     `json:"did"`
	Endpoint    string     `json:"endpoint"`
	PublicKey   crypto.JWK `json:"publicKey"`
	SubjectType string     `json:"subjectType"`
	TTL         int        `json:"ttl"`
	IssuedAt    int64      `json:"issuedAt"`
	Version     string     `json:"shadownet:v"`
}
