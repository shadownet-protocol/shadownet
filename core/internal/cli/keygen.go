// SPDX-License-Identifier: MIT

package cli

import (
	"flag"
	"fmt"
	"io"

	"github.com/shadownet-protocol/shadownet/core/internal/crypto"
	"github.com/shadownet-protocol/shadownet/core/internal/identifiers"
)

// Keygen implements `shadownet keygen`. It generates an Ed25519 keypair,
// optionally writes the private JWK to disk (mode 0600), and prints the
// public key in Shadownet's canonical multibase form (z6Mk… per RFC 0001
// §3.1 and §3.3). The printed value is what operators paste into provider
// DNS `pk=` records, Issuer key configuration, and trust-store entries.
func Keygen(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("keygen", flag.ContinueOnError)
	fs.SetOutput(stderr)
	out := fs.String("out", "", "path to write the private JWK (mode 0600)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	kp, err := crypto.Generate()
	if err != nil {
		return err
	}
	pub, err := identifiers.EncodePubKey(kp.Public)
	if err != nil {
		return err
	}
	fmt.Fprintln(stdout, pub)
	if *out != "" {
		if err := crypto.SaveKeyFile(*out, kp, pub); err != nil {
			return err
		}
		fmt.Fprintln(stdout, "wrote", *out, "(mode 0600)")
	}
	return nil
}
