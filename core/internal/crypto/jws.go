// SPDX-License-Identifier: MIT

package crypto

import (
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"

	jose "github.com/go-jose/go-jose/v4"
)

// AlgEdDSA is the only signature algorithm Shadownet permits per RFC-0001 §Cryptography.
const AlgEdDSA = jose.EdDSA

// ErrUnsupportedAlgorithm is returned when a JWS or JWT carries any signature
// algorithm other than EdDSA.
var ErrUnsupportedAlgorithm = errors.New("crypto: unsupported algorithm; only EdDSA is permitted")

// SignerOptions are the JWS protected-header fields a caller may set.
//
// KeyID (the "kid" header) is mandatory: every signed Shadownet artifact is
// keyed off a DID URL.
type SignerOptions struct {
	KeyID string
	Type  string // "typ" header; e.g. "JWT", "vc+jwt", "vp+jwt".
}

// JWSHeader is the parsed protected header of a verified compact JWS.
type JWSHeader struct {
	Alg string
	Typ string
	Kid string
}

// SignJWS signs payload with priv, producing a compact JWS serialization.
func SignJWS(priv ed25519.PrivateKey, payload []byte, opts SignerOptions) (string, error) {
	if len(priv) != ed25519.PrivateKeySize {
		return "", fmt.Errorf("crypto: private key length = %d, want %d", len(priv), ed25519.PrivateKeySize)
	}
	if opts.KeyID == "" {
		return "", errors.New("crypto: SignerOptions.KeyID is required")
	}
	so := (&jose.SignerOptions{}).WithHeader(jose.HeaderKey("kid"), opts.KeyID)
	if opts.Type != "" {
		so = so.WithType(jose.ContentType(opts.Type))
	}
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: AlgEdDSA, Key: priv}, so)
	if err != nil {
		return "", fmt.Errorf("crypto: new signer: %w", err)
	}
	obj, err := signer.Sign(payload)
	if err != nil {
		return "", fmt.Errorf("crypto: sign: %w", err)
	}
	out, err := obj.CompactSerialize()
	if err != nil {
		return "", fmt.Errorf("crypto: serialize: %w", err)
	}
	return out, nil
}

// SignJWT marshals claims to JSON and signs the result. opts.Type defaults to
// "JWT" when not set; callers passing a VC ("vc+jwt") or VP ("vp+jwt") MUST
// set Type explicitly.
func SignJWT(priv ed25519.PrivateKey, claims any, opts SignerOptions) (string, error) {
	if opts.Type == "" {
		opts.Type = "JWT"
	}
	body, err := json.Marshal(claims)
	if err != nil {
		return "", fmt.Errorf("crypto: marshal claims: %w", err)
	}
	return SignJWS(priv, body, opts)
}

// VerifyJWS parses compact, verifies it under pub, and returns the protected
// header and payload. Any algorithm other than EdDSA is rejected.
func VerifyJWS(pub ed25519.PublicKey, compact string) (JWSHeader, []byte, error) {
	if len(pub) != ed25519.PublicKeySize {
		return JWSHeader{}, nil, fmt.Errorf("crypto: public key length = %d, want %d", len(pub), ed25519.PublicKeySize)
	}
	obj, err := jose.ParseSigned(compact, []jose.SignatureAlgorithm{AlgEdDSA})
	if err != nil {
		return JWSHeader{}, nil, fmt.Errorf("crypto: parse jws: %w", err)
	}
	if len(obj.Signatures) != 1 {
		return JWSHeader{}, nil, fmt.Errorf("crypto: jws has %d signatures, want 1", len(obj.Signatures))
	}
	sig := obj.Signatures[0]
	if sig.Header.Algorithm != string(AlgEdDSA) {
		return JWSHeader{}, nil, fmt.Errorf("%w: got %q", ErrUnsupportedAlgorithm, sig.Header.Algorithm)
	}
	payload, err := obj.Verify(pub)
	if err != nil {
		return JWSHeader{}, nil, fmt.Errorf("crypto: verify: %w", err)
	}
	hdr := JWSHeader{Alg: sig.Header.Algorithm, Kid: sig.Header.KeyID}
	if v, ok := sig.Header.ExtraHeaders[jose.HeaderType]; ok {
		if s, ok := v.(string); ok {
			hdr.Typ = s
		}
	}
	return hdr, payload, nil
}

// VerifyJWT verifies the compact JWS and unmarshals the payload into out.
// out may be nil when the caller only cares about the header.
func VerifyJWT(pub ed25519.PublicKey, compact string, out any) (JWSHeader, error) {
	hdr, payload, err := VerifyJWS(pub, compact)
	if err != nil {
		return JWSHeader{}, err
	}
	if out != nil {
		if err := json.Unmarshal(payload, out); err != nil {
			return hdr, fmt.Errorf("crypto: unmarshal claims: %w", err)
		}
	}
	return hdr, nil
}

// PeekHeader returns the protected header of a compact JWS without verifying
// the signature. Useful for routing on `kid` before key resolution.
func PeekHeader(compact string) (JWSHeader, error) {
	obj, err := jose.ParseSigned(compact, []jose.SignatureAlgorithm{AlgEdDSA})
	if err != nil {
		return JWSHeader{}, fmt.Errorf("crypto: parse jws: %w", err)
	}
	if len(obj.Signatures) != 1 {
		return JWSHeader{}, fmt.Errorf("crypto: jws has %d signatures, want 1", len(obj.Signatures))
	}
	sig := obj.Signatures[0]
	hdr := JWSHeader{Alg: sig.Header.Algorithm, Kid: sig.Header.KeyID}
	if v, ok := sig.Header.ExtraHeaders[jose.HeaderType]; ok {
		if s, ok := v.(string); ok {
			hdr.Typ = s
		}
	}
	return hdr, nil
}
