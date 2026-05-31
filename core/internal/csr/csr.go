// SPDX-License-Identifier: MIT

// Package csr mints and parses shadownet-csr+jwt — the certificate signing
// request the Subject submits to the Issuer's /.well-known/shadownet/issue
// endpoint to obtain an org_affiliation credential.
//
// CSR identifiers (iss, aud, req.org) accept the same union of forms as
// credential identifiers (Shadowname, domain, multibase Ed25519 pubkey);
// the schema mirrors shadownet-specs/schemas/credentials/csr.schema.json.
//
// Spec: RFC 0001 §6.5.
package csr

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/wellknown"
)

// MaxLifetime is the §6.5 RECOMMENDED upper bound on exp - iat (10 minutes).
// Issuers MAY enforce a tighter cap; the value here is the wire-level
// maximum we'll mint or accept by default.
const MaxLifetime = 10 * time.Minute

// DefaultLifetime is the default ceremony window if the caller doesn't
// set one explicitly.
const DefaultLifetime = 5 * time.Minute

// DefaultLeeway is the ±60s clock skew tolerance per RFC 0001 §2.
const DefaultLeeway = 60 * time.Second

// Sentinel errors. Callers MAY wrap them with their own context; the
// spec-level reason MUST stay attached for downstream tooling.
var (
	ErrInvalid          = errors.New("csr: invalid")
	ErrLifetimeExceeded = errors.New("csr: lifetime exceeds maximum")
	ErrSignature        = errors.New("csr: signature did not verify")
	ErrExpired          = errors.New("csr: expired")
	ErrIATInFuture      = errors.New("csr: iat in the future")
	ErrAudienceMismatch = errors.New("csr: aud does not match this issuer")
)

// Request is the inner `req` object — the credential being asked for.
type Request struct {
	Kind string `json:"kind"`
	Org  string `json:"org"`
}

// Payload is the decoded claims of a shadownet-csr+jwt.
type Payload struct {
	Iss string  `json:"iss"`
	Aud string  `json:"aud"`
	Iat int64   `json:"iat"`
	Exp int64   `json:"exp"`
	Req Request `json:"req"`
}

// Mint signs `p` with the Subject's key, producing a JWS-compact
// shadownet-csr+jwt.
func Mint(p Payload, subject crypto.KeyPair) (string, error) {
	if p.Exp <= p.Iat {
		return "", fmt.Errorf("%w: exp must be greater than iat", ErrInvalid)
	}
	if time.Duration(p.Exp-p.Iat)*time.Second > MaxLifetime {
		return "", fmt.Errorf("%w: exp - iat = %ds", ErrLifetimeExceeded, p.Exp-p.Iat)
	}
	if identifiers.Classify(p.Iss) == identifiers.ClassUnknown {
		return "", fmt.Errorf("%w: iss %q is not a domain, shadowname, or multibase pubkey", ErrInvalid, p.Iss)
	}
	if identifiers.Classify(p.Aud) == identifiers.ClassUnknown {
		return "", fmt.Errorf("%w: aud %q is not a domain or pubkey", ErrInvalid, p.Aud)
	}
	if p.Req.Kind == "" {
		return "", fmt.Errorf("%w: req.kind required", ErrInvalid)
	}
	if identifiers.Classify(p.Req.Org) == identifiers.ClassUnknown {
		return "", fmt.Errorf("%w: req.org %q is not a domain or pubkey", ErrInvalid, p.Req.Org)
	}
	pubMB, err := identifiers.EncodePubKey(subject.Public)
	if err != nil {
		return "", fmt.Errorf("csr: encode subject pubkey: %w", err)
	}
	return crypto.SignJWT(subject.Private, p, crypto.SignerOptions{
		Type:  wellknown.TypShadownetCSRJWT,
		KeyID: kidFromSubject(p.Iss, pubMB),
	})
}

func kidFromSubject(iss, subjectPubMB string) string {
	switch identifiers.Classify(iss) {
	case identifiers.ClassPubKey:
		return iss
	case identifiers.ClassShadowname:
		return iss
	default:
		// Defensive: fall back to the pubkey itself.
		return subjectPubMB
	}
}

// VerifyOptions threads the issuer-side configuration into Verify.
type VerifyOptions struct {
	// Now overrides time.Now for deterministic testing.
	Now func() time.Time

	// Leeway tolerates clock skew on iat/exp. Defaults to DefaultLeeway when zero.
	Leeway time.Duration

	// ExpectedAudience is the issuer's own identifier (domain or pubkey).
	// Verify rejects CSRs whose `aud` doesn't match.
	ExpectedAudience string

	// ResolveSubjectKey returns the Subject's verification key for the
	// given iss identifier. For keyed-Subject CSRs the identifier IS the
	// key; callers can return identifiers.DecodePubKey(iss) directly. For
	// Shadowname-mode Subjects the caller looks up the signing key from
	// the Subject's AgentCard.
	ResolveSubjectKey func(iss string) (ed25519.PublicKey, error)
}

// Verify parses, validates, and authenticates a shadownet-csr+jwt.
func Verify(token string, opts VerifyOptions) (Payload, error) {
	hdr, err := crypto.PeekHeader(token)
	if err != nil {
		return Payload{}, fmt.Errorf("%w: header: %v", ErrInvalid, err)
	}
	if hdr.Typ != wellknown.TypShadownetCSRJWT {
		return Payload{}, fmt.Errorf("%w: typ %q, want %q", ErrInvalid, hdr.Typ, wellknown.TypShadownetCSRJWT)
	}
	if hdr.Alg != "EdDSA" {
		return Payload{}, fmt.Errorf("%w: alg %q, want EdDSA", ErrInvalid, hdr.Alg)
	}

	segs := strings.Split(token, ".")
	if len(segs) != 3 {
		return Payload{}, fmt.Errorf("%w: malformed compact serialization", ErrInvalid)
	}
	payload, err := base64.RawURLEncoding.DecodeString(segs[1])
	if err != nil {
		return Payload{}, fmt.Errorf("%w: payload base64: %v", ErrInvalid, err)
	}
	var p Payload
	if err := json.Unmarshal(payload, &p); err != nil {
		return Payload{}, fmt.Errorf("%w: payload json: %v", ErrInvalid, err)
	}

	if opts.ExpectedAudience == "" {
		return Payload{}, fmt.Errorf("%w: VerifyOptions.ExpectedAudience required", ErrInvalid)
	}
	if p.Aud != opts.ExpectedAudience {
		return Payload{}, fmt.Errorf("%w: aud=%q want=%q", ErrAudienceMismatch, p.Aud, opts.ExpectedAudience)
	}
	if p.Exp <= p.Iat {
		return Payload{}, fmt.Errorf("%w: exp must be greater than iat", ErrInvalid)
	}
	if time.Duration(p.Exp-p.Iat)*time.Second > MaxLifetime {
		return Payload{}, ErrLifetimeExceeded
	}
	if identifiers.Classify(p.Iss) == identifiers.ClassUnknown {
		return Payload{}, fmt.Errorf("%w: iss %q is not a recognized identifier", ErrInvalid, p.Iss)
	}
	if p.Req.Kind == "" {
		return Payload{}, fmt.Errorf("%w: req.kind required", ErrInvalid)
	}
	if identifiers.Classify(p.Req.Org) == identifiers.ClassUnknown {
		return Payload{}, fmt.Errorf("%w: req.org %q is not a recognized identifier", ErrInvalid, p.Req.Org)
	}

	now := time.Now
	if opts.Now != nil {
		now = opts.Now
	}
	leeway := opts.Leeway
	if leeway == 0 {
		leeway = DefaultLeeway
	}
	nowT := now()
	if nowT.Unix() > p.Exp+int64(leeway/time.Second) {
		return Payload{}, ErrExpired
	}
	if nowT.Unix()+int64(leeway/time.Second) < p.Iat {
		return Payload{}, ErrIATInFuture
	}

	if opts.ResolveSubjectKey == nil {
		return Payload{}, fmt.Errorf("%w: VerifyOptions.ResolveSubjectKey required", ErrInvalid)
	}
	pub, err := opts.ResolveSubjectKey(p.Iss)
	if err != nil {
		return Payload{}, fmt.Errorf("csr: resolve subject key for %q: %w", p.Iss, err)
	}
	if _, _, err := crypto.VerifyJWS(pub, token); err != nil {
		return Payload{}, fmt.Errorf("%w: %v", ErrSignature, err)
	}
	return p, nil
}
