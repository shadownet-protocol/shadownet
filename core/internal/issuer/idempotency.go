// SPDX-License-Identifier: MIT

package issuer

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"

	"github.com/shadownet-protocol/shadownet/core/internal/jcs"
)

// IdempotencyKey is SHA-256(iss || 0x1F || aud || 0x1F || JCS(req)). The
// 0x1F unit separator guards against ambiguity-by-concatenation; JCS on
// the req object guarantees the same key for two equivalent requests
// regardless of map iteration order at mint time. RFC 0001 §6.5 leaves
// the key shape unspecified — this derivation is implementation-defined
// but matches python-sdk's mirror.
func IdempotencyKey(iss, aud string, req map[string]any) (string, error) {
	if iss == "" || aud == "" {
		return "", fmt.Errorf("%w: iss and aud both required for idempotency", ErrInvalid)
	}
	if len(req) == 0 {
		return "", fmt.Errorf("%w: req object required for idempotency", ErrInvalid)
	}
	reqCanon, err := jcs.Canonicalize(req)
	if err != nil {
		return "", fmt.Errorf("issuer: canonicalize req: %w", err)
	}
	h := sha256.New()
	h.Write([]byte(iss))
	h.Write([]byte{0x1F})
	h.Write([]byte(aud))
	h.Write([]byte{0x1F})
	h.Write(reqCanon)
	return hex.EncodeToString(h.Sum(nil)), nil
}
