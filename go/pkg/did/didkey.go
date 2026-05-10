// SPDX-License-Identifier: MIT

package did

import (
	"crypto/ed25519"
	"errors"
	"fmt"
	"strings"
)

// DID method names.
const (
	MethodKey = "key"
	MethodWeb = "web"
)

const didKeyPrefix = "did:key:"

// ed25519MulticodecPrefix is the unsigned-varint encoding of multicodec 0xed
// ("ed25519-pub"): 0xed > 0x7f, so it splits as [0xed, 0x01].
var ed25519MulticodecPrefix = [2]byte{0xed, 0x01}

// EncodeKey returns the did:key DID for an Ed25519 public key.
func EncodeKey(pub ed25519.PublicKey) (string, error) {
	if len(pub) != ed25519.PublicKeySize {
		return "", fmt.Errorf("did: public key length = %d, want %d", len(pub), ed25519.PublicKeySize)
	}
	body := make([]byte, 0, 2+ed25519.PublicKeySize)
	body = append(body, ed25519MulticodecPrefix[:]...)
	body = append(body, pub...)
	return didKeyPrefix + "z" + base58Encode(body), nil
}

// DecodeKey parses a did:key DID and returns the embedded Ed25519 public key.
//
// Any DID URL fragment (e.g. "#0") is permitted and ignored — the DID
// identity is in the body, not the fragment.
func DecodeKey(did string) (ed25519.PublicKey, error) {
	if !strings.HasPrefix(did, didKeyPrefix) {
		return nil, fmt.Errorf("did: not a did:key: %q", did)
	}
	rest := did[len(didKeyPrefix):]
	if i := strings.IndexByte(rest, '#'); i >= 0 {
		rest = rest[:i]
	}
	if rest == "" {
		return nil, errors.New("did: did:key body is empty")
	}
	if rest[0] != 'z' {
		return nil, fmt.Errorf("did: did:key must use base58btc multibase prefix 'z', got %q", rest[0])
	}
	raw, err := base58Decode(rest[1:])
	if err != nil {
		return nil, fmt.Errorf("did: decode did:key: %w", err)
	}
	if len(raw) != 2+ed25519.PublicKeySize {
		return nil, fmt.Errorf("did: did:key body length = %d, want %d", len(raw), 2+ed25519.PublicKeySize)
	}
	if raw[0] != ed25519MulticodecPrefix[0] || raw[1] != ed25519MulticodecPrefix[1] {
		return nil, fmt.Errorf("did: did:key multicodec = 0x%02x%02x, want 0xed01", raw[0], raw[1])
	}
	out := make(ed25519.PublicKey, ed25519.PublicKeySize)
	copy(out, raw[2:])
	return out, nil
}

// Method returns the DID method (e.g. "key", "web") of a DID URL, or "" if
// the input is not a syntactically valid DID.
func Method(did string) string {
	if !strings.HasPrefix(did, "did:") {
		return ""
	}
	rest := did[len("did:"):]
	i := strings.IndexByte(rest, ':')
	if i < 0 {
		return ""
	}
	return rest[:i]
}

// SplitDIDURL splits a DID URL into (DID, fragment). Fragment is "" when absent.
func SplitDIDURL(didURL string) (didStr, fragment string) {
	if i := strings.IndexByte(didURL, '#'); i >= 0 {
		return didURL[:i], didURL[i+1:]
	}
	return didURL, ""
}
