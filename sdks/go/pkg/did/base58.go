// SPDX-License-Identifier: MIT

package did

import (
	"fmt"
	"math/big"
)

// base58btc alphabet, as defined for Bitcoin.
const base58Alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

// base58Decode reverses base58Encode. The alphabet is base58btc.
//
// We carry a small implementation rather than pull a dep — base58 is the only
// non-stdlib codec we need and it is a few dozen lines. See
// CLAUDE.md "Dependencies" for the dep-bias rationale.
func base58Encode(in []byte) string {
	if len(in) == 0 {
		return ""
	}
	leading := 0
	for leading < len(in) && in[leading] == 0 {
		leading++
	}
	n := new(big.Int).SetBytes(in)
	base := big.NewInt(58)
	rem := new(big.Int)

	out := make([]byte, 0, len(in)*2)
	for n.Sign() > 0 {
		n.DivMod(n, base, rem)
		out = append(out, base58Alphabet[rem.Int64()])
	}
	for i := 0; i < leading; i++ {
		out = append(out, base58Alphabet[0])
	}
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return string(out)
}

func base58Decode(s string) ([]byte, error) {
	if s == "" {
		return nil, nil
	}
	leading := 0
	for leading < len(s) && s[leading] == base58Alphabet[0] {
		leading++
	}
	n := big.NewInt(0)
	base := big.NewInt(58)
	d := big.NewInt(0)
	for i := 0; i < len(s); i++ {
		idx := indexBase58(s[i])
		if idx < 0 {
			return nil, fmt.Errorf("did: invalid base58 character %q", s[i])
		}
		n.Mul(n, base)
		d.SetInt64(int64(idx))
		n.Add(n, d)
	}
	raw := n.Bytes()
	out := make([]byte, leading+len(raw))
	copy(out[leading:], raw)
	return out, nil
}

func indexBase58(c byte) int {
	for i := 0; i < len(base58Alphabet); i++ {
		if base58Alphabet[i] == c {
			return i
		}
	}
	return -1
}
