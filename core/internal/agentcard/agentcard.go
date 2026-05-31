// SPDX-License-Identifier: MIT

// Package agentcard builds and signs A2A AgentCards with the Shadownet
// extension fields (RFC 0001 §5.3). The Provider uses it to host
// Shadowname-mode cards at <ep>/identity/<local>; direct-mode Shadows and
// keyed Issuers use it to self-serve cards at /.well-known/agent-card.json.
//
// Signing follows A2A §8.4: strip the `signatures` field and any
// empty/default values, JCS-canonicalize the result, sign the JWS-detached
// signing input (BASE64URL(header) || "." || BASE64URL(payload)) with the
// signer's Ed25519 key, and attach the resulting {protected, signature}
// pair to a freshly populated `signatures` array.
//
// Shadownet narrows the A2A signature surface:
//
//   - alg MUST be EdDSA (RFC 0001 §4.1).
//   - typ MUST be JOSE (A2A §8.4.2).
//   - kid is shadownet@<provider-domain> for Shadowname-mode (RFC 0001
//     §5.2) or the Shadow's multibase pubkey for direct-mode (RFC 0001
//     §5.3).
package agentcard

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
	"github.com/shadownet-protocol/shadownet/core/internal/jcs"
	"github.com/shadownet-protocol/shadownet/core/internal/wellknown"
)

// ErrInvalid wraps every Build/Sign/Verify failure in this package so
// callers can errors.Is against a single sentinel.
var ErrInvalid = errors.New("agentcard: invalid")

// Mode picks the kid scheme + the security-scheme inlining.
type Mode int

const (
	// ModeShadowname is the multi-tenant Provider-hosted form. kid is
	// "shadownet@<provider-domain>"; no pinned-self-signed declaration.
	ModeShadowname Mode = iota
	// ModeDirect is the direct-addressed (no DNS) form. kid is the
	// Shadow's multibase pubkey; the card declares
	// securitySchemes.shadownet:pinned-self-signed (RFC 0001 §4.1, §5.4)
	// to flag the non-WebPKI TLS posture.
	ModeDirect
)

// Body is the input shape callers fill in. The package fills in
// shadownet:v, shadownet:pk, and the required Shadownet capabilities
// extension automatically.
type Body struct {
	Name               string
	Description        string
	Version            string
	A2AURL             string
	A2AProtocolBinding string
	A2AProtocolVersion string
	ShadowPublicKey    string // multibase Ed25519, "z6Mk…"
	IssueEndpoint      string // optional: keyed-Hub Issuer (RFC 0001 §6.5)
	StatusListBase     string // optional: keyed-Hub Issuer (RFC 0001 §6.4)
	AdditionalSecurity map[string]any
	Extras             map[string]any // additional top-level fields (rarely needed)
}

// Build returns the unsigned AgentCard body shape per RFC 0001 §5.3 + §5.4.
// Caller signs via Sign or BuildAndSign.
func Build(b Body) (map[string]any, error) {
	if b.ShadowPublicKey == "" {
		return nil, fmt.Errorf("%w: shadow public key required", ErrInvalid)
	}
	if identifiers.Classify(b.ShadowPublicKey) != identifiers.ClassPubKey {
		return nil, fmt.Errorf("%w: shadow public key %q is not a multibase pubkey", ErrInvalid, b.ShadowPublicKey)
	}
	if b.A2AURL == "" {
		return nil, fmt.Errorf("%w: A2A URL required", ErrInvalid)
	}
	if b.A2AProtocolBinding == "" {
		b.A2AProtocolBinding = "HTTP+JSON"
	}
	if b.A2AProtocolVersion == "" {
		b.A2AProtocolVersion = "1.0"
	}
	if b.Version == "" {
		b.Version = "1.0.0"
	}

	body := map[string]any{
		"name":        b.Name,
		"description": b.Description,
		"version":     b.Version,
		"supportedInterfaces": []any{
			map[string]any{
				"url":             b.A2AURL,
				"protocolBinding": b.A2AProtocolBinding,
				"protocolVersion": b.A2AProtocolVersion,
			},
		},
		"capabilities": map[string]any{
			"extensions": []any{
				map[string]any{
					"uri":         wellknown.ExtensionURI,
					"required":    true,
					"description": "Shadownet identity envelope",
				},
			},
		},
		wellknown.FieldShadownetV:  wellknown.ProtocolVersion,
		wellknown.FieldShadownetPK: b.ShadowPublicKey,
	}
	if b.IssueEndpoint != "" {
		body[wellknown.FieldShadownetIssueEndpoint] = b.IssueEndpoint
	}
	if b.StatusListBase != "" {
		body[wellknown.FieldShadownetStatusListBase] = b.StatusListBase
	}
	if len(b.AdditionalSecurity) > 0 || hasPinnedSelfSigned(b.AdditionalSecurity) {
		body["securitySchemes"] = b.AdditionalSecurity
	}
	for k, v := range b.Extras {
		body[k] = v
	}
	return body, nil
}

func hasPinnedSelfSigned(m map[string]any) bool {
	_, ok := m[wellknown.SecuritySchemePinnedSelfSign]
	return ok
}

// AddPinnedSelfSigned sets the canonical Shadownet self-signed-TLS marker
// in a Body's AdditionalSecurity map. Direct-mode self-served cards SHOULD
// call this before Build (RFC 0001 §4.1, §5.4).
func (b *Body) AddPinnedSelfSigned() {
	if b.AdditionalSecurity == nil {
		b.AdditionalSecurity = make(map[string]any, 1)
	}
	b.AdditionalSecurity[wellknown.SecuritySchemePinnedSelfSign] = map[string]any{
		"type":        "shadownet:pinned-self-signed",
		"description": "Self-signed TLS certificate; clients pin SHA-256 fingerprint on first use (TOFU) or against the #sha256: URI fragment.",
	}
}

// Sign attaches a §8.4 JWS signature to the body and returns the result.
// The input body MUST NOT already carry a `signatures` field (Sign will
// reject it — re-signing is an explicit operation handled by stripping
// signatures upstream).
func Sign(body map[string]any, signer ed25519.PrivateKey, mode Mode, providerDomain, shadowPubMB string) (map[string]any, error) {
	if _, hasSig := body["signatures"]; hasSig {
		return nil, fmt.Errorf("%w: body already has a signatures field", ErrInvalid)
	}
	if len(signer) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("%w: signer key is not an Ed25519 private key", ErrInvalid)
	}

	pruned := stripEmpty(body)
	if pruned == nil {
		return nil, fmt.Errorf("%w: canonical form is empty", ErrInvalid)
	}
	payload, err := jcs.Canonicalize(pruned)
	if err != nil {
		return nil, fmt.Errorf("agentcard: canonicalize: %w", err)
	}
	payloadB64 := base64.RawURLEncoding.EncodeToString(payload)

	var kid string
	switch mode {
	case ModeShadowname:
		if providerDomain == "" {
			return nil, fmt.Errorf("%w: provider domain required for ModeShadowname", ErrInvalid)
		}
		if identifiers.Classify(providerDomain) != identifiers.ClassDomain {
			return nil, fmt.Errorf("%w: provider domain %q is not a domain", ErrInvalid, providerDomain)
		}
		kid = "shadownet@" + providerDomain
	case ModeDirect:
		if shadowPubMB == "" {
			return nil, fmt.Errorf("%w: shadow public key required for ModeDirect", ErrInvalid)
		}
		if identifiers.Classify(shadowPubMB) != identifiers.ClassPubKey {
			return nil, fmt.Errorf("%w: shadow public key %q is not a multibase pubkey", ErrInvalid, shadowPubMB)
		}
		kid = shadowPubMB
	default:
		return nil, fmt.Errorf("%w: unknown signing mode", ErrInvalid)
	}

	header := map[string]any{
		"alg": "EdDSA",
		"typ": wellknown.TypJOSE,
		"kid": kid,
	}
	headerBytes, err := json.Marshal(header)
	if err != nil {
		return nil, fmt.Errorf("agentcard: marshal header: %w", err)
	}
	headerB64 := base64.RawURLEncoding.EncodeToString(headerBytes)
	signingInput := headerB64 + "." + payloadB64
	sig := ed25519.Sign(signer, []byte(signingInput))
	sigB64 := base64.RawURLEncoding.EncodeToString(sig)

	out := make(map[string]any, len(body)+1)
	for k, v := range body {
		out[k] = v
	}
	out["signatures"] = []any{
		map[string]any{
			"protected": headerB64,
			"signature": sigB64,
		},
	}
	return out, nil
}

// VerifyOptions is the verifier-side counterpart to Sign.
type VerifyOptions struct {
	// ExpectedKID is the kid the verifier requires. Shadowname-mode
	// verifiers pass "shadownet@<provider-domain>"; direct-mode verifiers
	// pass the embedded multibase pubkey from the connection URI.
	ExpectedKID string

	// CandidateKeys are the public keys to try in order. Provider DNS may
	// publish multiple pk= entries during a rotation grace window; pass
	// all of them and Verify accepts the first that validates.
	CandidateKeys []ed25519.PublicKey
}

// Verify checks the A2A §8.4 signature on `card` against opts.CandidateKeys.
// The verifier rebuilds the canonical payload from the supplied card body
// (after stripping signatures and empty/default values), so caller-side
// transport encoding has no influence on the signed bytes.
func Verify(card map[string]any, opts VerifyOptions) error {
	if opts.ExpectedKID == "" {
		return fmt.Errorf("%w: VerifyOptions.ExpectedKID required", ErrInvalid)
	}
	if len(opts.CandidateKeys) == 0 {
		return fmt.Errorf("%w: VerifyOptions.CandidateKeys empty", ErrInvalid)
	}
	sigs, ok := card["signatures"].([]any)
	if !ok || len(sigs) == 0 {
		return fmt.Errorf("%w: card has no signatures", ErrInvalid)
	}

	pruned := stripEmpty(stripSignatures(card))
	if pruned == nil {
		return fmt.Errorf("%w: canonical form is empty", ErrInvalid)
	}
	payload, err := jcs.Canonicalize(pruned)
	if err != nil {
		return fmt.Errorf("agentcard: canonicalize: %w", err)
	}
	payloadB64 := base64.RawURLEncoding.EncodeToString(payload)

	var lastErr error
	for _, sig := range sigs {
		entry, ok := sig.(map[string]any)
		if !ok {
			continue
		}
		protected, _ := entry["protected"].(string)
		signature, _ := entry["signature"].(string)
		if protected == "" || signature == "" {
			continue
		}
		headerBytes, err := base64.RawURLEncoding.DecodeString(protected)
		if err != nil {
			lastErr = fmt.Errorf("%w: protected header base64: %v", ErrInvalid, err)
			continue
		}
		var header struct {
			Alg string `json:"alg"`
			Typ string `json:"typ"`
			Kid string `json:"kid"`
		}
		if err := json.Unmarshal(headerBytes, &header); err != nil {
			lastErr = fmt.Errorf("%w: protected header json: %v", ErrInvalid, err)
			continue
		}
		if header.Alg != "EdDSA" {
			lastErr = fmt.Errorf("%w: alg %q, want EdDSA", ErrInvalid, header.Alg)
			continue
		}
		if header.Typ != wellknown.TypJOSE {
			lastErr = fmt.Errorf("%w: typ %q, want %s", ErrInvalid, header.Typ, wellknown.TypJOSE)
			continue
		}
		if header.Kid != opts.ExpectedKID {
			lastErr = fmt.Errorf("%w: kid %q, want %q", ErrInvalid, header.Kid, opts.ExpectedKID)
			continue
		}
		sigBytes, err := base64.RawURLEncoding.DecodeString(signature)
		if err != nil {
			lastErr = fmt.Errorf("%w: signature base64: %v", ErrInvalid, err)
			continue
		}
		signingInput := protected + "." + payloadB64
		for _, pub := range opts.CandidateKeys {
			if ed25519.Verify(pub, []byte(signingInput), sigBytes) {
				return nil
			}
		}
		lastErr = fmt.Errorf("%w: signature did not verify against any candidate key", ErrInvalid)
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("%w: no parseable signatures", ErrInvalid)
	}
	return lastErr
}

// stripSignatures returns a copy of `card` with the `signatures` field
// removed. The remaining slots are shared with the input — callers MUST
// NOT mutate them.
func stripSignatures(card map[string]any) map[string]any {
	out := make(map[string]any, len(card))
	for k, v := range card {
		if k == "signatures" {
			continue
		}
		out[k] = v
	}
	return out
}

// stripEmpty mirrors the A2A §8.4.1 "remove empty values" pass we cross-
// checked against a2a-python's _clean_empty: drop empty strings, empty
// arrays, empty maps, and nils. Returns nil for fully-empty inputs.
func stripEmpty(v any) any {
	switch x := v.(type) {
	case nil:
		return nil
	case string:
		if x == "" {
			return nil
		}
		return x
	case []any:
		out := make([]any, 0, len(x))
		for _, item := range x {
			if cleaned := stripEmpty(item); cleaned != nil {
				out = append(out, cleaned)
			}
		}
		if len(out) == 0 {
			return nil
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, vv := range x {
			if cleaned := stripEmpty(vv); cleaned != nil {
				out[k] = cleaned
			}
		}
		if len(out) == 0 {
			return nil
		}
		return out
	default:
		return v
	}
}
