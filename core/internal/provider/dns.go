// SPDX-License-Identifier: MIT

package provider

import (
	"crypto/ed25519"
	"fmt"

	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
)

// TXTRecord returns the `_shadownet.<domain>` DNS TXT body the operator
// publishes to make the Provider discoverable per RFC 0001 §4.2.
//
// Multiple `pk=` values can be passed; the second-and-later entries
// support provider key rotation grace windows (verifiers MUST accept any
// of them). Optional `extras` slot in additional key=value pairs for
// `iss=true` (this domain operates an issuer) or `delegate=<domain>`
// (affiliation-issuer delegation).
func TXTRecord(endpoint string, providerPubs []ed25519.PublicKey, extras ...string) (string, error) {
	if endpoint == "" {
		return "", fmt.Errorf("provider: endpoint required")
	}
	if len(providerPubs) == 0 {
		return "", fmt.Errorf("provider: at least one provider public key required")
	}
	parts := []string{"v=0.2", "ep=" + endpoint}
	for _, pk := range providerPubs {
		mb, err := identifiers.EncodePubKey(pk)
		if err != nil {
			return "", fmt.Errorf("provider: encode pubkey: %w", err)
		}
		parts = append(parts, "pk="+mb)
	}
	parts = append(parts, extras...)
	out := ""
	for i, p := range parts {
		if i > 0 {
			out += "; "
		}
		out += p
	}
	return out, nil
}
