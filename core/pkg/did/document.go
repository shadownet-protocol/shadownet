// SPDX-License-Identifier: MIT

package did

import (
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/shadownet-protocol/shadownet/core/pkg/crypto"
)

// VerificationMethod is one Ed25519 key embedded in a DID document.
//
// Per RFC-0002 §Permitted, only Ed25519 keys are recognized at v0.1; any
// other verification-method type is filtered out at parse time.
type VerificationMethod struct {
	ID         string            // fully-qualified DID URL, e.g. "did:web:example.com#k1"
	Controller string            // DID that controls this method
	Public     ed25519.PublicKey // raw 32-byte Ed25519 public key
}

// Document is the resolved Shadownet view of a DID document.
//
// Only the fields RFC-0002 §Permitted lists are represented. DelegatedIssuers
// is the one organization-only extension; it is populated only when ID is a
// did:web (orgs), and silently dropped on did:key documents per the RFC.
type Document struct {
	ID                 string
	VerificationMethod []VerificationMethod
	Authentication     []string // verification-method IDs
	AssertionMethod    []string // verification-method IDs
	DelegatedIssuers   []string // shadownet:delegatedIssuers; orgs only
}

// IsDelegatedIssuer reports whether issuerDID is listed in DelegatedIssuers.
// Used by AffiliationCredential verifiers to confirm that an issuer DID is
// authorized to sign on behalf of this organization DID.
func (d *Document) IsDelegatedIssuer(issuerDID string) bool {
	for _, di := range d.DelegatedIssuers {
		if di == issuerDID {
			return true
		}
	}
	return false
}

// FindVerificationMethod returns the verification method whose ID matches
// either the full DID URL or just the fragment after '#'.
func (d *Document) FindVerificationMethod(idOrFragment string) (VerificationMethod, bool) {
	for _, vm := range d.VerificationMethod {
		if vm.ID == idOrFragment {
			return vm, true
		}
		if frag := fragmentOf(vm.ID); frag != "" && frag == idOrFragment {
			return vm, true
		}
	}
	return VerificationMethod{}, false
}

// rawDocument is the JSON wire shape we accept; intentionally minimal.
type rawDocument struct {
	ID                 string                  `json:"id"`
	VerificationMethod []rawVerificationMethod `json:"verificationMethod,omitempty"`
	Authentication     []verificationMethodRef `json:"authentication,omitempty"`
	AssertionMethod    []verificationMethodRef `json:"assertionMethod,omitempty"`
	DelegatedIssuers   []string                `json:"shadownet:delegatedIssuers,omitempty"`
}

type rawVerificationMethod struct {
	ID                 string      `json:"id"`
	Type               string      `json:"type"`
	Controller         string      `json:"controller"`
	PublicKeyMultibase string      `json:"publicKeyMultibase,omitempty"`
	PublicKeyJwk       *crypto.JWK `json:"publicKeyJwk,omitempty"`
}

// verificationMethodRef is either a string (reference to a VM by ID/fragment)
// or an embedded verification method. v0.1 only honors the string form.
type verificationMethodRef struct {
	Ref string
}

func (r *verificationMethodRef) UnmarshalJSON(b []byte) error {
	if len(b) > 0 && b[0] == '"' {
		var s string
		if err := json.Unmarshal(b, &s); err != nil {
			return err
		}
		r.Ref = s
		return nil
	}
	// Embedded verification methods are valid per W3C DID Core but unused at
	// v0.1; we ignore them rather than error out.
	r.Ref = ""
	return nil
}

func parseDocument(raw []byte) (*Document, error) {
	var rd rawDocument
	if err := json.Unmarshal(raw, &rd); err != nil {
		return nil, fmt.Errorf("did: parse document: %w", err)
	}
	if rd.ID == "" {
		return nil, errors.New("did: document missing required field id")
	}
	doc := &Document{ID: rd.ID}
	for _, vm := range rd.VerificationMethod {
		pub, ok := extractEd25519(vm)
		if !ok {
			continue
		}
		doc.VerificationMethod = append(doc.VerificationMethod, VerificationMethod{
			ID:         vm.ID,
			Controller: vm.Controller,
			Public:     pub,
		})
	}
	for _, r := range rd.Authentication {
		if r.Ref != "" {
			doc.Authentication = append(doc.Authentication, r.Ref)
		}
	}
	for _, r := range rd.AssertionMethod {
		if r.Ref != "" {
			doc.AssertionMethod = append(doc.AssertionMethod, r.Ref)
		}
	}
	if len(rd.DelegatedIssuers) > 0 && Method(rd.ID) == MethodWeb {
		doc.DelegatedIssuers = append([]string(nil), rd.DelegatedIssuers...)
	}
	return doc, nil
}

func extractEd25519(vm rawVerificationMethod) (ed25519.PublicKey, bool) {
	if vm.PublicKeyJwk != nil {
		pub, err := vm.PublicKeyJwk.Public()
		if err == nil {
			return pub, true
		}
	}
	if vm.PublicKeyMultibase != "" {
		pub, err := decodeMultibaseEd25519(vm.PublicKeyMultibase)
		if err == nil {
			return pub, true
		}
	}
	return nil, false
}

// decodeMultibaseEd25519 decodes a "z<base58btc>" multibase string and asserts
// the multicodec prefix is Ed25519 (0xed01).
func decodeMultibaseEd25519(s string) (ed25519.PublicKey, error) {
	if len(s) == 0 || s[0] != 'z' {
		return nil, fmt.Errorf("did: multibase must use 'z' prefix, got %q", s)
	}
	raw, err := base58Decode(s[1:])
	if err != nil {
		return nil, err
	}
	if len(raw) != 2+ed25519.PublicKeySize {
		return nil, fmt.Errorf("did: multibase body length = %d, want %d", len(raw), 2+ed25519.PublicKeySize)
	}
	if raw[0] != ed25519MulticodecPrefix[0] || raw[1] != ed25519MulticodecPrefix[1] {
		return nil, fmt.Errorf("did: multibase multicodec = 0x%02x%02x, want 0xed01", raw[0], raw[1])
	}
	out := make(ed25519.PublicKey, ed25519.PublicKeySize)
	copy(out, raw[2:])
	return out, nil
}

func fragmentOf(didURL string) string {
	for i := 0; i < len(didURL); i++ {
		if didURL[i] == '#' {
			return didURL[i+1:]
		}
	}
	return ""
}
