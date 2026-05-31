// SPDX-License-Identifier: MIT

package provider

import (
	"crypto/sha256"
	"encoding/hex"
)

// ETag returns the strong validator for a signed AgentCard body. Computed
// over the canonical (already-JSON-encoded) byte slice so that ETag
// stability tracks signature stability — two cards with different
// signatures produce different ETags, two cards with the same signature
// produce the same ETag.
func ETag(body []byte) string {
	sum := sha256.Sum256(body)
	return `"` + hex.EncodeToString(sum[:]) + `"`
}
