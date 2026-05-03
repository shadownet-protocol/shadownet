// SPDX-License-Identifier: MIT

// Package keyguard refuses to boot a reference server when its signing key
// matches one of the well-known fixture keys committed to
// shadownet-conformance/fixtures/.
//
// Those fixtures are public test material — anyone can derive their private
// keys from the seeds in fixtures/seeds.toml. A production server that loads
// one as its signing key would issue forgeable credentials. This guard
// exists to fail closed in that case.
//
// To opt out (for the conformance suite's self-test, or for an explicit
// private dev deploy): set SHADOWNET_ALLOW_FIXTURE_KEYS=1 in the environment.
package keyguard

import (
	"crypto/ed25519"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"os"
)

// AllowEnv is the environment-variable name a deployment can set to bypass
// the guard.
const AllowEnv = "SHADOWNET_ALLOW_FIXTURE_KEYS"

// fixturePublicKeys are the raw 32-byte Ed25519 public keys derived from the
// seeds in shadownet-conformance/fixtures/seeds.toml. Recompute via:
//
//	for f in shadownet-conformance/fixtures/keys/*.json; do
//	    jq -r '.public_jwk.x' "$f" | base64 -d | xxd -p -c 32
//	done
//
// Order is incidental; the table is small and we linear-scan.
var fixturePublicKeys = []string{
	// peer_holder
	"1ba4075b77c9e3fb3ecde15cdaf5221f3c10373e623f7b0e1ef76366b0af7137",
	// sca_issuer
	"5c9c6df261c9cb840475776aaefcd944b405328fab28f9b3a95ef40490d3de84",
	// sns_provider
	"4ed32f63bf35f0eeefcb25f28a2e1fbdc873ae2835671b0c9460f5f12e4556a8",
	// subject_acme
	"ed4928c628d1c2c6eae90338905995612959273a5c63f93636c14614ac8737d1",
	// subject_alice
	"8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c",
	// subject_bob
	"8139770ea87d175f56a35466c34c7ecccb8d8a91b4ee37a25df60f5b8fc9b394",
}

// IsFixtureKey reports whether pub is byte-equal to a known conformance
// fixture public key. Constant-time comparison is unnecessary here — these
// values are public — but cheap and harmless.
func IsFixtureKey(pub ed25519.PublicKey) bool {
	if len(pub) != ed25519.PublicKeySize {
		return false
	}
	for _, hexKey := range fixturePublicKeys {
		raw, err := hex.DecodeString(hexKey)
		if err != nil || len(raw) != ed25519.PublicKeySize {
			continue
		}
		if subtle.ConstantTimeCompare(pub, raw) == 1 {
			return true
		}
	}
	return false
}

// AssertNotFixture returns an error describing how to fix things if pub is a
// fixture key and the override env var is not set. Callers should treat the
// error as fatal at startup.
func AssertNotFixture(pub ed25519.PublicKey, role string) error {
	if !IsFixtureKey(pub) {
		return nil
	}
	if os.Getenv(AllowEnv) == "1" {
		return nil
	}
	return fmt.Errorf(
		"%s signing key matches a public fixture from shadownet-conformance "+
			"(see fixtures/seeds.toml). Booting with this key would let any "+
			"reader of that repo forge artifacts that appear to come from this "+
			"deployment. Generate a fresh key with `shadownet keygen` and point "+
			"signing.keyfile at it. To override (e.g. for the conformance suite's "+
			"self-test against this binary), set %s=1",
		role, AllowEnv,
	)
}
